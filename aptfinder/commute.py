"""Car commute times to and from work using the Google Routes API.

For each listing we ask for four routes, all on a typical weekday with
predicted traffic:
  - home -> work leaving at 8:15, allowing tolls (I-405 express toll lanes) and avoiding them
  - work -> home leaving at 17:00, allowing tolls and avoiding them
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .db import Database

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.duration,routes.distanceMeters,routes.description,routes.travelAdvisory.tollInfo"
METERS_PER_MILE = 1609.344


class CommuteError(RuntimeError):
    pass


def next_typical_weekday(today: date) -> date:
    """The next Tuesday or Wednesday after today (avoids Monday/Friday traffic quirks)."""
    d = today + timedelta(days=1)
    while d.weekday() not in (1, 2):
        d += timedelta(days=1)
    return d


def departure_iso(day: date, hhmm: str, tz: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime.combine(day, time(h, m), tzinfo=ZoneInfo(tz))
    return local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(m: dict) -> float:
    return int(m.get("units", 0) or 0) + (m.get("nanos", 0) or 0) / 1e9


def parse_route(resp: dict) -> dict[str, Any]:
    routes = resp.get("routes") or []
    if not routes:
        raise CommuteError("No driving route found for this address.")
    r = routes[0]
    seconds = int(str(r.get("duration", "0s")).rstrip("s") or 0)
    tolls = sum(
        _money(p)
        for p in (r.get("travelAdvisory", {}).get("tollInfo", {}).get("estimatedPrice") or [])
        if p.get("currencyCode", "USD") == "USD"
    )
    return {
        "minutes": round(seconds / 60, 1),
        "miles": round(r.get("distanceMeters", 0) / METERS_PER_MILE, 1),
        "toll_usd": round(tolls, 2),
        "via": r.get("description") or "",
    }


def _route(client: httpx.Client, api_key: str, origin: str, dest: str,
           depart: str, avoid_tolls: bool) -> dict[str, Any]:
    body = {
        "origin": {"address": origin},
        "destination": {"address": dest},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "departureTime": depart,
        "extraComputations": ["TOLLS"],
        "routeModifiers": {"avoidTolls": avoid_tolls, "tollPasses": ["US_WA_GOOD_TO_GO"]},
        "units": "IMPERIAL",
    }
    resp = client.post(
        ROUTES_URL,
        json=body,
        headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK},
        timeout=20,
    )
    if resp.status_code != 200:
        try:
            msg = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            msg = resp.text
        raise CommuteError(f"Google Routes API error {resp.status_code}: {msg[:200]}")
    return parse_route(resp.json())


def summarize(legs: dict[str, dict], settings: Settings) -> dict[str, Any]:
    """Pick the route to use each way and estimate monthly driving cost.

    The toll route is used only when it saves at least 5 minutes over the free route.
    """
    out: dict[str, Any] = {"legs": legs}
    total_minutes = 0.0
    total_miles = 0.0
    total_tolls = 0.0
    for direction in ("to_work", "to_home"):
        free, toll = legs[f"{direction}_free"], legs[f"{direction}_toll"]
        use_toll = toll["toll_usd"] > 0 and free["minutes"] - toll["minutes"] >= 5
        chosen = toll if use_toll else free
        out[direction] = {**chosen, "uses_toll": use_toll,
                          "minutes_saved_by_toll": round(free["minutes"] - toll["minutes"], 1)}
        total_minutes += chosen["minutes"]
        total_miles += chosen["miles"]
        total_tolls += chosen["toll_usd"] if use_toll else 0
    days = settings.workdays_per_month
    out["daily_minutes"] = round(total_minutes, 1)
    out["monthly_gas"] = round(total_miles * days * settings.cost_per_mile, 2)
    out["monthly_tolls"] = round(total_tolls * days, 2)
    out["monthly_cost"] = round(out["monthly_gas"] + out["monthly_tolls"], 2)
    return out


def compute_commute(address: str, settings: Settings, db: Database, force: bool = False,
                    http: httpx.Client | None = None, today: date | None = None) -> dict[str, Any]:
    if not settings.google_maps_api_key:
        raise CommuteError("GOOGLE_MAPS_API_KEY is not set.")
    if not address:
        raise CommuteError("This listing has no address yet.")

    day = next_typical_weekday(today or datetime.now(ZoneInfo(settings.timezone)).date())
    plan = {
        "to_work": (address, settings.work_address, departure_iso(day, settings.depart_home, settings.timezone)),
        "to_home": (settings.work_address, address, departure_iso(day, settings.depart_work, settings.timezone)),
    }
    key = hashlib.sha256(
        f"{address}|{settings.work_address}|{settings.depart_home}|{settings.depart_work}".lower().encode()
    ).hexdigest()
    if not force and (cached := db.cache_get(key)):
        return cached

    owns_client = http is None
    http = http or httpx.Client()
    try:
        legs = {}
        for direction, (o, d, t) in plan.items():
            legs[f"{direction}_toll"] = _route(http, settings.google_maps_api_key, o, d, t, avoid_tolls=False)
            legs[f"{direction}_free"] = _route(http, settings.google_maps_api_key, o, d, t, avoid_tolls=True)
    except httpx.HTTPError as e:
        raise CommuteError(f"Could not reach Google Routes API: {e}") from e
    finally:
        if owns_client:
            http.close()

    result = summarize(legs, settings) | {
        "sample_day": day.isoformat(),
        "depart_home": settings.depart_home,
        "depart_work": settings.depart_work,
        "work_address": settings.work_address,
    }
    db.cache_put(key, result)
    return result
