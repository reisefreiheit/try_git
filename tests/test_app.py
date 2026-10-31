import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from aptfinder import assessor
from aptfinder.address import normalize_address
from aptfinder.commute import compute_commute, next_typical_weekday, parse_route
from aptfinder.config import Settings
from aptfinder.construction import construction_score
from aptfinder.db import Database
from aptfinder.extract import FIELDS, ExtractionError, extract_listing
from aptfinder.fit import costs, must_have_checks
from aptfinder.main import create_app

TODAY = date(2026, 10, 31)


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "t.db", anthropic_api_key=None, google_maps_api_key=None)


@pytest.fixture
def db(settings):
    return Database(settings.db_path)


# ---- address --------------------------------------------------------------

def test_normalize_address():
    assert normalize_address("12345 Northeast 85th Street, Unit 210, Kirkland") == "12345 NE 85TH ST"
    assert normalize_address("12345 NE 85th St #210") == "12345 NE 85TH ST"
    assert normalize_address("100 Bellevue Way Southeast Apt B-3") == "100 BELLEVUE WAY SE"


# ---- construction score ---------------------------------------------------

def test_score_none_without_data():
    r = construction_score(None, None, None, today=TODAY)
    assert r["score"] is None and r["confidence"] is None


def test_score_high_confidence_with_county_and_tour():
    building = {"year_built": 2016, "constr_class": "Wood frame", "bldg_quality": "Good",
                "condition": "Good", "stories": 6}
    tour = {k: 4 for k in ["wall_noise", "street_noise", "water", "windows", "moisture"]}
    r = construction_score(building, None, tour, today=TODAY)
    assert r["confidence"] == "high"
    assert 65 <= r["score"] <= 85
    texts = " ".join(f["text"] for f in r["flags"])
    assert "HB 1217" in texts  # 10 years old
    assert "wood over a concrete base" in texts
    assert sum(c["weight"] for c in r["components"]) in (99, 100, 101)


def test_score_era_flags_and_low_tour_rating():
    building = {"year_built": 1968, "constr_class": "4", "bldg_quality": "Average", "condition": "Fair"}
    r = construction_score(building, None, {"moisture": 1}, today=TODAY)
    texts = " ".join(f["text"] for f in r["flags"])
    assert "lead paint" in texts and "aluminum" in texts and "older windows" in texts
    assert "No moisture or mold signs rated 1/5" in texts
    assert r["confidence"] == "medium"


def test_leaky_era_and_renovation():
    r = construction_score({"year_built": 1990, "eff_year": 2012, "constr_class": "Wood frame"},
                           None, None, today=TODAY)
    levels = {f["level"] for f in r["flags"]}
    texts = " ".join(f["text"] for f in r["flags"])
    assert "leaky building" in texts and "good" in levels
    assert r["confidence"] == "low"


def test_listing_year_used_as_fallback():
    r = construction_score(None, 2005, None, today=TODAY)
    assert r["score"] is not None and r["components"][0]["key"] == "age"


def test_checklist_rejects_out_of_range():
    with pytest.raises(ValueError):
        construction_score(None, None, {"water": 7})


# ---- fit / costs ----------------------------------------------------------

def test_must_haves_and_costs():
    listing = {"rent": 2850, "bedrooms": 1, "in_unit_laundry": True, "gym": None,
               "covered_parking": False, "parking_cost": 150, "utilities_estimate": None,
               "monthly_fees": 75, "deposit": 1000, "move_in_fees": 350}
    checks = {c["label"]: c["status"] for c in must_have_checks(listing, Settings().must_haves)}
    assert checks == {"1+ bedroom": "pass", "Rent under $3,000": "pass", "In-unit laundry": "pass",
                      "Gym in building": "unknown", "Covered parking": "fail"}
    c = costs(listing, {"monthly_cost": 120})
    assert c["monthly_total"] == 2850 + 150 + 75 + 120
    assert c["monthly_unknown"] == ["utilities"]
    assert c["move_in_total"] == 2850 + 1000 + 350


# ---- commute --------------------------------------------------------------

def test_next_typical_weekday():
    assert next_typical_weekday(date(2026, 10, 31)) == date(2026, 11, 3)  # Sat -> Tue
    assert next_typical_weekday(date(2026, 11, 3)) == date(2026, 11, 4)   # Tue -> Wed


def test_parse_route_tolls():
    r = parse_route({"routes": [{"duration": "1500s", "distanceMeters": 16093,
                                 "description": "I-405 N",
                                 "travelAdvisory": {"tollInfo": {"estimatedPrice": [
                                     {"currencyCode": "USD", "units": "3", "nanos": 500000000}]}}}]})
    assert r == {"minutes": 25.0, "miles": 10.0, "toll_usd": 3.5, "via": "I-405 N"}


def test_compute_commute_picks_toll_only_when_worth_it(settings, db):
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        to_work = body["destination"]["address"] == settings.work_address
        avoid = body["routeModifiers"]["avoidTolls"]
        if to_work:  # toll saves 12 minutes
            route = {"duration": "1680s" if avoid else "960s", "distanceMeters": 20000}
            if not avoid:
                route["travelAdvisory"] = {"tollInfo": {"estimatedPrice": [{"currencyCode": "USD", "units": "4"}]}}
        else:  # toll saves 2 minutes
            route = {"duration": "1320s" if avoid else "1200s", "distanceMeters": 20000}
            if not avoid:
                route["travelAdvisory"] = {"tollInfo": {"estimatedPrice": [{"currencyCode": "USD", "units": "2"}]}}
        return httpx.Response(200, json={"routes": [route]})

    s = replace(settings, google_maps_api_key="k")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    r = compute_commute("1 Main St, Kirkland, WA", s, db, http=http, today=TODAY)
    assert len(calls) == 4
    assert calls[0]["departureTime"] == "2026-11-03T16:15:00Z"  # 8:15 PST
    assert r["to_work"]["uses_toll"] and not r["to_home"]["uses_toll"]
    assert r["monthly_tolls"] == 4 * 21
    # cached on second call
    compute_commute("1 Main St, Kirkland, WA", s, db, http=http, today=TODAY)
    assert len(calls) == 4


def test_commute_requires_key(settings, db):
    from aptfinder.commute import CommuteError
    with pytest.raises(CommuteError):
        compute_commute("1 Main St", settings, db)


# ---- extraction -----------------------------------------------------------

class FakeAnthropic:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.kwargs = payload, stop_reason, None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def test_extract_listing(settings):
    payload = {k: None for k in FIELDS} | {"rent": 2700, "address": "1 Main St", "red_flags": []}
    fake = FakeAnthropic(payload)
    out = extract_listing("some listing", settings, client=fake)
    assert out["rent"] == 2700 and out["red_flags"] == []
    assert fake.kwargs["output_config"]["format"]["type"] == "json_schema"
    assert fake.kwargs["model"] == settings.claude_model


def test_extract_refusal(settings):
    with pytest.raises(ExtractionError):
        extract_listing("x", settings, client=FakeAnthropic({}, stop_reason="refusal"))


def test_extract_without_key(settings):
    with pytest.raises(ExtractionError):
        extract_listing("x", settings)


# ---- assessor import ------------------------------------------------------

CSV = (
    "Major,Minor,ComplexDescr,NbrStories,NbrUnits,ConstrClass,BldgQuality,Condition,YrBuilt,EffYr,Address,ZipCode\n"
    "123456,0010,THE EXAMPLE APTS,6,200,4,5,4,2016,2016,12345 NE 85TH ST,98033\n"
    "654321,0020,OLD GARDEN APTS,2,40,4,3,2,1972,1990,12340 NE 85TH ST,98033\n"
)


def test_assessor_import_and_match(tmp_path, db):
    p = tmp_path / "EXTR_AptComplex.csv"
    p.write_text(CSV)
    assert assessor.import_file(db, "apartment", p) == 2
    cands = assessor.match_candidates(db, "12345 Northeast 85th Street #210")
    assert cands[0]["name"] == "The Example Apts"
    assert cands[0]["constr_class"] == "Wood frame" and cands[0]["bldg_quality"] == "Good"
    assert cands[0]["parcel"] == "1234560010"


def test_assessor_missing_columns(tmp_path, db):
    p = tmp_path / "bad.csv"
    p.write_text("Foo,Bar\n1,2\n")
    with pytest.raises(assessor.ImportError_):
        assessor.import_file(db, "apartment", p)


# ---- API ------------------------------------------------------------------

def test_api_flow(tmp_path, settings, db):
    payload = {k: None for k in FIELDS} | {
        "title": "The Example", "address": "12345 NE 85th St", "city": "Kirkland", "rent": 2795,
        "bedrooms": 1, "in_unit_laundry": True, "gym": True, "covered_parking": True,
        "red_flags": ["asks for wire transfer"],
    }
    client = TestClient(create_app(settings, db, anthropic_client=FakeAnthropic(payload)))

    r = client.post("/api/listings", json={"text": "listing text", "url": "https://example.com/a"})
    assert r.status_code == 201, r.text
    created = r.json()
    lid = created["id"]
    assert created["rent"] == 2795 and created["url"] == "https://example.com/a"
    assert all(m["status"] == "pass" for m in created["must_haves"])
    assert created["red_flags"] == ["asks for wire transfer"]
    assert any("Commute not calculated" in w for w in created["warnings"])

    r = client.patch(f"/api/listings/{lid}", json={"status": "toured", "parking_cost": 0})
    assert r.json()["status"] == "toured"
    assert client.patch(f"/api/listings/{lid}", json={"status": "bogus"}).status_code == 422
    assert client.patch(f"/api/listings/{lid}", json={"nope": 1}).status_code == 422

    p = tmp_path / "apt.csv"
    p.write_text(CSV)
    assessor.import_file(db, "apartment", p)
    cands = client.get(f"/api/listings/{lid}/assessor-matches").json()
    r = client.put(f"/api/listings/{lid}/building", json={"assessor_id": cands[0]["id"]})
    assert r.json()["construction"]["confidence"] == "medium"

    ratings = {"wall_noise": 3, "street_noise": 4, "water": 5, "windows": 4, "moisture": 5}
    r = client.put(f"/api/listings/{lid}/checklist", json={"ratings": ratings, "notes": "quiet"})
    assert r.json()["construction"]["confidence"] == "high"
    assert client.put(f"/api/listings/{lid}/checklist", json={"ratings": {"water": 9}}).status_code == 422

    assert len(client.get("/api/listings").json()) == 1
    assert client.get("/api/config").json()["assessor_records"] == 2
    assert client.get("/").status_code == 200
    assert client.delete(f"/api/listings/{lid}").status_code == 204
    assert client.get(f"/api/listings/{lid}").status_code == 404


def test_api_manual_entry_without_keys(settings, db):
    client = TestClient(create_app(settings, db))
    r = client.post("/api/listings", json={"text": "x", "fields": {"address": "1 Main St", "rent": 3100}})
    body = r.json()
    assert r.status_code == 201
    assert any("extraction skipped" in w for w in body["warnings"])
    rent_check = next(m for m in body["must_haves"] if m["label"].startswith("Rent"))
    assert rent_check["status"] == "fail"
