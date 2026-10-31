"""Listing photos: storage on disk and construction-quality analysis with Claude.

The browser shrinks each photo to at most 1024 px on the long edge and converts it
to JPEG before upload, which keeps the per-listing Claude cost around 10-15 cents
for ten photos.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import Any

import anthropic

from .config import Settings

MAX_PHOTOS = 20
MAX_BYTES = 5 * 1024 * 1024
MAGIC = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png", b"RIFF": "image/webp"}
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# What Claude rates, and which tour-checklist item (if any) overrides it.
CATEGORIES: list[tuple[str, str, str | None]] = [
    ("windows", "Windows", "windows"),
    ("flooring", "Flooring", "floors_doors"),
    ("kitchen", "Kitchen", "finishes"),
    ("bathroom", "Bathroom", "finishes"),
    ("hvac", "Heating and cooling", "hvac"),
    ("moisture", "Water and moisture signs", "moisture"),
    ("exterior", "Exterior and balconies", None),
    ("renovation", "Renovation consistency", None),
]
CATEGORY_KEYS = [k for k, _, _ in CATEGORIES]


class PhotoError(RuntimeError):
    pass


# ---- storage ----------------------------------------------------------------

def photo_dir(settings: Settings, listing_id: int) -> Path:
    return Path(settings.db_path).parent / "photos" / str(int(listing_id))


def detect_type(data: bytes) -> str | None:
    for magic, mt in MAGIC.items():
        if data.startswith(magic):
            if mt == "image/webp" and data[8:12] != b"WEBP":
                return None
            return mt
    return None


def save_photos(settings: Settings, listing_id: int, existing: list[str],
                images_b64: list[str]) -> tuple[list[str], list[str]]:
    """Validate and save uploads. Returns (all file names, error messages)."""
    folder = photo_dir(settings, listing_id)
    folder.mkdir(parents=True, exist_ok=True)
    names = list(existing)
    errors = []
    next_n = max((int(n.split(".")[0]) for n in names), default=0) + 1
    for i, b64 in enumerate(images_b64, 1):
        if len(names) >= MAX_PHOTOS:
            errors.append(f"Only {MAX_PHOTOS} photos per listing; extra photos were skipped.")
            break
        try:
            data = base64.b64decode(b64.split(",", 1)[-1], validate=True)
        except (ValueError, TypeError):
            errors.append(f"Upload {i} was not a valid image.")
            continue
        mt = detect_type(data)
        if not mt:
            errors.append(f"Upload {i} is not a JPEG, PNG or WebP image.")
            continue
        if len(data) > MAX_BYTES:
            errors.append(f"Upload {i} is larger than 5 MB.")
            continue
        name = f"{next_n}.{EXT[mt]}"
        (folder / name).write_bytes(data)
        names.append(name)
        next_n += 1
    return names, errors


def delete_photo(settings: Settings, listing_id: int, name: str) -> None:
    path = photo_dir(settings, listing_id) / Path(name).name
    path.unlink(missing_ok=True)


def delete_all(settings: Settings, listing_id: int) -> None:
    shutil.rmtree(photo_dir(settings, listing_id), ignore_errors=True)


# ---- analysis ---------------------------------------------------------------

def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


_CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "visible": {"type": "boolean"},
        "rating": _nullable({"type": "integer"}),
        "evidence": {"type": "string"},
        "photos": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["visible", "rating", "evidence", "photos"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "object",
            "properties": {k: _CATEGORY_SCHEMA for k in CATEGORY_KEYS},
            "required": CATEGORY_KEYS,
            "additionalProperties": False,
        },
        "check_on_tour": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"photo": _nullable({"type": "integer"}), "text": {"type": "string"}},
                "required": ["photo", "text"],
                "additionalProperties": False,
            },
        },
        "photo_caveats": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["categories", "check_on_tour", "photo_caveats", "summary"],
    "additionalProperties": False,
}

SYSTEM = """You assess the construction and finish quality of a rental apartment from its \
listing photos, for someone deciding whether to tour it. Photos are numbered in the order given.

Rate each category 1-5 only from what is clearly visible; otherwise set visible=false and \
rating=null. Never rate from assumptions about the building's age or price.
- windows: 5 = vinyl or fiberglass double-pane in good shape; 1 = thin aluminum single-pane, \
fogging between panes, or damaged.
- flooring: 5 = hardwood, quality vinyl plank or tile with clean transitions; 1 = worn or \
stained carpet, peeling vinyl, uneven seams.
- kitchen: cabinet construction, counters (stone/quartz vs. peeling laminate), appliances, \
backsplash and caulking.
- bathroom: tile vs. one-piece plastic surround, caulk and grout condition, visible exhaust \
fan, fixtures.
- hvac: 5 = ductless mini-split or heat pump (implies cooling); 3 = forced air with no \
visible cooling; 1 = baseboard heaters only or a window AC unit.
- moisture: 5 = no signs anywhere; lower for ceiling or wall stains, bubbling paint, \
discoloration around windows, visible mold.
- exterior: siding, balconies and decks, landscaping, signs of rot or patching.
- renovation: 5 = consistent quality throughout; lower for "lipstick" updates, such as new \
counters and paint next to original windows, fixtures or electrical.

evidence: one short sentence naming what you saw, citing photo numbers. photos: the photo \
numbers you relied on.
check_on_tour: specific things to inspect in person, with the photo number when one \
prompted it (e.g. "Possible ceiling stain above the bedroom window - check for active leaks").
photo_caveats: note anything that limits what the photos show: wide-angle or heavily edited \
shots, virtual staging, 3D renderings, photos that look like a model unit rather than the \
actual unit, or important areas not shown.
summary: two sentences at most on overall build and finish quality as shown."""


def analyze_photos(paths: list[Path], settings: Settings, client: Any | None = None) -> dict[str, Any]:
    if not paths:
        raise PhotoError("Add photos first.")
    if client is None:
        if not settings.anthropic_api_key:
            raise PhotoError("ANTHROPIC_API_KEY is not set.")
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    content: list[dict[str, Any]] = []
    for n, path in enumerate(paths, 1):
        data = path.read_bytes()
        content.append({"type": "text", "text": f"Photo {n}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": detect_type(data) or "image/jpeg",
            "data": base64.standard_b64encode(data).decode("ascii"),
        }})
    content.append({"type": "text", "text": "Assess these listing photos."})

    try:
        response = client.beta.messages.create(
            model=settings.claude_model,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as e:
        raise PhotoError("Anthropic API key was rejected.") from e
    except anthropic.RateLimitError as e:
        raise PhotoError("Rate limited by the Anthropic API; try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise PhotoError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise PhotoError("Could not reach the Anthropic API.") from e

    if response.stop_reason == "refusal":
        raise PhotoError("The model declined to analyze these photos.")
    if response.stop_reason == "max_tokens":
        raise PhotoError("The analysis was cut off; try fewer photos.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise PhotoError("No result returned.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise PhotoError("Result was not valid JSON.") from e
    return clean_analysis(raw, len(paths))


def clean_analysis(raw: dict[str, Any], n_photos: int) -> dict[str, Any]:
    """Clamp ratings to 1-5 and drop photo numbers that don't exist."""
    def ok_photo(p: Any) -> bool:
        return isinstance(p, int) and 1 <= p <= n_photos

    cats = {}
    for key in CATEGORY_KEYS:
        c = (raw.get("categories") or {}).get(key) or {}
        rating = c.get("rating")
        if not c.get("visible") or not isinstance(rating, int):
            rating = None
        else:
            rating = min(5, max(1, rating))
        cats[key] = {
            "visible": rating is not None,
            "rating": rating,
            "evidence": (c.get("evidence") or "").strip(),
            "photos": [p for p in c.get("photos") or [] if ok_photo(p)],
        }
    return {
        "categories": cats,
        "check_on_tour": [
            {"photo": t.get("photo") if ok_photo(t.get("photo")) else None, "text": t["text"].strip()}
            for t in raw.get("check_on_tour") or [] if (t.get("text") or "").strip()
        ],
        "photo_caveats": [s.strip() for s in raw.get("photo_caveats") or [] if s.strip()],
        "summary": (raw.get("summary") or "").strip(),
        "photo_count": n_photos,
    }


def photo_component(analysis: dict[str, Any] | None,
                    tour_ratings: dict[str, int]) -> tuple[float, str, list[dict]] | None:
    """Score 0-100 from visible photo ratings, skipping categories the tour already rated.

    Returns (score, detail, per-category breakdown) or None if nothing usable.
    """
    if not analysis:
        return None
    used, breakdown = [], []
    for key, label, tour_key in CATEGORIES:
        c = analysis["categories"].get(key) or {}
        overridden = bool(tour_key and tour_key in tour_ratings)
        if c.get("rating") is not None and not overridden:
            used.append(c["rating"])
        breakdown.append({"key": key, "label": label, **c, "overridden_by_tour": overridden})
    if not used:
        return None
    avg = sum(used) / len(used)
    detail = f"{len(used)} categories visible in {analysis.get('photo_count', '?')} photos, average {avg:.1f}/5"
    return (avg - 1) / 4 * 100, detail, breakdown
