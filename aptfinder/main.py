"""Web app: FastAPI backend plus a single static page.

Run:  uvicorn aptfinder.main:create_app --factory   then open http://127.0.0.1:8000
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assessor, photos
from .commute import CommuteError, compute_commute
from .config import Settings, get_settings
from .construction import CHECKLIST, clean_checklist, construction_score
from .db import LISTING_FIELDS, STATUSES, Database
from .extract import ExtractionError, extract_listing
from .fit import costs, must_have_checks

STATIC = Path(__file__).parent / "static"


class NewListing(BaseModel):
    text: str | None = None
    url: str | None = None
    extract: bool = True
    fields: dict[str, Any] = Field(default_factory=dict)


class BuildingIn(BaseModel):
    assessor_id: int | None = None
    year_built: int | None = None
    eff_year: int | None = None
    stories: int | None = None
    constr_class: str | None = None
    bldg_quality: str | None = None
    condition: str | None = None
    name: str | None = None
    parcel: str | None = None
    notes: str | None = None


class PhotosIn(BaseModel):
    images: list[str] = Field(default_factory=list, max_length=photos.MAX_PHOTOS)


class ChecklistIn(BaseModel):
    ratings: dict[str, int | None] = Field(default_factory=dict)
    notes: str | None = None


def create_app(settings: Settings | None = None, db: Database | None = None,
               anthropic_client: Any | None = None, http: httpx.Client | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = db or Database(settings.db_path)
    app = FastAPI(title="Eastside Apartment Finder")

    def enrich(listing: dict[str, Any]) -> dict[str, Any]:
        checklist = listing.get("checklist") or {}
        return {
            **listing,
            "must_haves": must_have_checks(listing, settings.must_haves),
            "costs": costs(listing, listing.get("commute") if not (listing.get("commute") or {}).get("error") else None),
            "construction": construction_score(
                listing.get("building"), listing.get("year_built"), checklist.get("ratings"),
                (listing.get("photos") or {}).get("analysis"),
            ),
        }

    def get_or_404(listing_id: int) -> dict[str, Any]:
        listing = db.get_listing(listing_id)
        if not listing:
            raise HTTPException(404, "Listing not found")
        return listing

    def try_commute(listing: dict[str, Any], force: bool = False) -> dict[str, Any]:
        address = ", ".join(p for p in (listing.get("address"), listing.get("city"), "WA") if p)
        try:
            result = compute_commute(address if listing.get("address") else "", settings, db,
                                     force=force, http=http)
        except CommuteError as e:
            result = {"error": str(e)}
        return db.set_listing_json(listing["id"], "commute_json", result)

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        m = settings.must_haves
        return {
            "work_address": settings.work_address,
            "depart_home": settings.depart_home,
            "depart_work": settings.depart_work,
            "workdays_per_month": settings.workdays_per_month,
            "must_haves": m.__dict__,
            "statuses": STATUSES,
            "checklist": [{"key": k, "label": l, "hint": h} for k, l, h in CHECKLIST],
            "has_anthropic_key": bool(settings.anthropic_api_key or anthropic_client),
            "has_google_key": bool(settings.google_maps_api_key),
            "assessor_records": db.assessor_count(),
            "editable_fields": LISTING_FIELDS,
        }

    @app.get("/api/listings")
    def list_listings() -> list[dict[str, Any]]:
        return [enrich(x) for x in db.list_listings()]

    @app.post("/api/listings", status_code=201)
    def create_listing(body: NewListing) -> dict[str, Any]:
        warnings: list[str] = []
        extracted: dict[str, Any] | None = None
        if body.text and body.extract:
            try:
                extracted = extract_listing(body.text, settings, client=anthropic_client)
            except ExtractionError as e:
                warnings.append(f"Automatic extraction skipped: {e}")
        fields = {k: v for k, v in (extracted or {}).items() if k in LISTING_FIELDS and v is not None}
        fields.update({k: v for k, v in body.fields.items() if v not in (None, "")})
        if body.url:
            fields["url"] = body.url
        if not fields.get("address") and not fields.get("title"):
            fields["title"] = "Untitled listing"
        listing = db.create_listing(fields, source_text=body.text, extracted=extracted)
        listing = try_commute(listing)
        if (listing.get("commute") or {}).get("error"):
            warnings.append(f"Commute not calculated: {listing['commute']['error']}")
        return {**enrich(listing), "warnings": warnings,
                "red_flags": (extracted or {}).get("red_flags", [])}

    @app.get("/api/listings/{listing_id}")
    def get_listing(listing_id: int) -> dict[str, Any]:
        return enrich(get_or_404(listing_id))

    @app.patch("/api/listings/{listing_id}")
    def update_listing(listing_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        before = get_or_404(listing_id)
        unknown = set(fields) - set(LISTING_FIELDS)
        if unknown:
            raise HTTPException(422, f"Unknown fields: {', '.join(sorted(unknown))}")
        try:
            listing = db.update_listing(listing_id, fields)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        if (before.get("address"), before.get("city")) != (listing.get("address"), listing.get("city")):
            listing = try_commute(listing)
        return enrich(listing)

    @app.delete("/api/listings/{listing_id}", status_code=204)
    def delete_listing(listing_id: int) -> None:
        if not db.delete_listing(listing_id):
            raise HTTPException(404, "Listing not found")
        photos.delete_all(settings, listing_id)

    @app.post("/api/listings/{listing_id}/commute")
    def refresh_commute(listing_id: int) -> dict[str, Any]:
        return enrich(try_commute(get_or_404(listing_id), force=True))

    @app.get("/api/listings/{listing_id}/assessor-matches")
    def assessor_matches(listing_id: int) -> list[dict[str, Any]]:
        listing = get_or_404(listing_id)
        return assessor.match_candidates(db, listing.get("address") or "")

    @app.put("/api/listings/{listing_id}/building")
    def set_building(listing_id: int, body: BuildingIn) -> dict[str, Any]:
        get_or_404(listing_id)
        if body.assessor_id is not None:
            rec = db.get_assessor_record(body.assessor_id)
            if not rec:
                raise HTTPException(404, "County record not found")
            building = assessor.record_to_building(rec)
        else:
            building = {"source": "manual", **body.model_dump(exclude={"assessor_id"}, exclude_none=True)}
            if len(building) == 1:
                building = None
        return enrich(db.set_listing_json(listing_id, "building_json", building))

    @app.put("/api/listings/{listing_id}/checklist")
    def set_checklist(listing_id: int, body: ChecklistIn) -> dict[str, Any]:
        get_or_404(listing_id)
        try:
            ratings = clean_checklist(body.ratings)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e)) from e
        return enrich(db.set_listing_json(listing_id, "checklist_json",
                                          {"ratings": ratings, "notes": body.notes or ""}))

    def photo_state(listing: dict[str, Any]) -> dict[str, Any]:
        return listing.get("photos") or {"files": [], "analysis": None, "analyzed_files": []}

    @app.post("/api/listings/{listing_id}/photos")
    def add_photos(listing_id: int, body: PhotosIn) -> dict[str, Any]:
        state = photo_state(get_or_404(listing_id))
        files, errors = photos.save_photos(settings, listing_id, state["files"], body.images)
        listing = db.set_listing_json(listing_id, "photos_json", {**state, "files": files})
        return {**enrich(listing), "warnings": errors}

    @app.delete("/api/listings/{listing_id}/photos/{name}")
    def remove_photo(listing_id: int, name: str) -> dict[str, Any]:
        state = photo_state(get_or_404(listing_id))
        if name not in state["files"]:
            raise HTTPException(404, "Photo not found")
        photos.delete_photo(settings, listing_id, name)
        files = [f for f in state["files"] if f != name]
        return enrich(db.set_listing_json(listing_id, "photos_json", {**state, "files": files}))

    @app.get("/api/listings/{listing_id}/photos/{name}")
    def get_photo(listing_id: int, name: str) -> Response:
        state = photo_state(get_or_404(listing_id))
        if name not in state["files"]:
            raise HTTPException(404, "Photo not found")
        path = photos.photo_dir(settings, listing_id) / name
        if not path.exists():
            raise HTTPException(404, "Photo file missing")
        return FileResponse(path)

    @app.post("/api/listings/{listing_id}/photos/analyze")
    def analyze(listing_id: int) -> dict[str, Any]:
        state = photo_state(get_or_404(listing_id))
        folder = photos.photo_dir(settings, listing_id)
        paths = [folder / f for f in state["files"] if (folder / f).exists()]
        try:
            analysis = photos.analyze_photos(paths, settings, client=anthropic_client)
        except photos.PhotoError as e:
            raise HTTPException(400, str(e)) from e
        new_state = {**state, "analysis": analysis, "analyzed_files": [p.name for p in paths]}
        return enrich(db.set_listing_json(listing_id, "photos_json", new_state))

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
