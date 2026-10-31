"""Construction-quality score (0-100) with confidence and plain-language flags.

This is a screening estimate from county records, building-era patterns and
your own tour observations. It is not a substitute for a building inspection.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .photos import photo_component

CHECKLIST = [
    ("wall_noise", "Sound between units", "Knock on shared walls; listen for neighbors' TV, voices and footsteps above."),
    ("street_noise", "Street and freeway noise", "With windows closed. Matters near I-405, SR-522 and SR-520."),
    ("water", "Water pressure and hot water", "Run shower and sink together; time how long hot water takes."),
    ("windows", "Windows", "Double-pane, no fog between panes, seals tight, open and lock easily."),
    ("moisture", "No moisture or mold signs", "Ceilings, under sinks, window sills, closets on outside walls; musty smell."),
    ("floors_doors", "Floors and doors", "No soft or bouncy spots; doors close square without rubbing."),
    ("hvac", "Heating and cooling", "Heat pump or AC vs. baseboard only; thermostat works; vents clean."),
    ("finishes", "Kitchen and bath finishes", "Cabinet build, counters, fixtures, caulking, tile work."),
    ("common_areas", "Common areas and garage", "Hallways, gym, garage, trash rooms: clean and maintained?"),
]
CHECKLIST_KEYS = {k for k, _, _ in CHECKLIST}

QUALITY_SCORES = {
    "low": 0, "low/average": 20, "average": 45, "average/good": 60,
    "good": 75, "good/excellent": 88, "excellent": 100,
}
CONDITION_SCORES = {"poor": 0, "fair": 30, "average": 55, "good": 80, "excellent": 100}

# Sums to 100 when every input is present. Photos stay low: they are marketing shots.
WEIGHTS = {"quality": 27, "condition": 13, "structure": 9, "age": 11, "tour": 30, "photos": 10}
LABELS = {
    "quality": "County building quality grade",
    "condition": "County condition rating",
    "structure": "Construction type",
    "age": "Age (from renovation year if any)",
    "tour": "Your tour checklist",
    "photos": "Listing photos (Claude)",
}


def _norm_label(s: str | None) -> str:
    return " ".join((s or "").lower().replace(" / ", "/").split())


def _structure(constr: str | None, year: int | None) -> tuple[int | None, str | None]:
    c = _norm_label(constr)
    if not c:
        return None, None
    if "steel" in c and "prefab" not in c:
        return 95, "Steel frame: strongest for fire, quiet and durable."
    if "concrete" in c:
        return 90, "Reinforced concrete: good sound isolation and durability."
    if "masonry" in c:
        if year and year < 1950:
            return 30, "Older masonry: possible unreinforced masonry, a known earthquake risk."
        return 55, "Masonry."
    if "wood" in c:
        return 65, "Wood frame: the most common type; sound between units varies a lot."
    if "prefab" in c:
        return 50, "Prefab steel."
    return None, constr


def _flags(year: int | None, eff_year: int | None, constr: str | None, stories: int | None,
           quality: str | None, condition: str | None, checklist: dict[str, int],
           this_year: int) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    add = lambda level, text: flags.append({"level": level, "text": text})  # noqa: E731
    wood = "wood" in _norm_label(constr)
    renovated = bool(year and eff_year and eff_year - year >= 10)

    if year:
        age = this_year - year
        if age < 12:
            add("info", f"Built {year}: under 12 years old, so it is exempt from Washington's rent-increase "
                        "cap (HB 1217). Renewal increases can be larger; ask about typical increases.")
        if year < 1978:
            add("warn", f"Built {year}: before 1978, so lead paint may be present. The landlord must give a "
                        "lead disclosure.")
        if 1965 <= year <= 1973:
            add("warn", "Built 1965-1973, when aluminum branch wiring was sometimes used. Ask whether "
                        "wiring is copper or has been remediated.")
        if 1978 <= year <= 1995:
            add("info", "Built 1978-1995, when polybutylene water pipes were sometimes used. Ask whether "
                        "the building has been repiped.")
        if year < 1980 and not renovated:
            add("warn", "Pre-1980 with no major renovation on record: likely older windows and less "
                        "insulation. Expect higher heating bills and more outside noise.")
        if 1985 <= year <= 2005 and wood:
            add("warn", "Wood frame built 1985-2005, the Northwest 'leaky building' era before rainscreen "
                        "walls were standard. Look for water stains and ask about siding/envelope repairs.")
        if year >= 2010 and wood and (stories or 0) >= 5:
            add("info", "Newer 5+ story wood-frame building, likely wood over a concrete base. Energy code "
                        "is good, but sound between units is often the weak point: test it on the tour.")
    if renovated:
        add("good", f"County records show major renovation (effective year {eff_year}, built {year}).")
    if "masonry" in _norm_label(constr) and year and year < 1950:
        add("warn", "Masonry building from before 1950: ask whether it has had a seismic retrofit.")
    q = _norm_label(quality)
    if q in ("good", "good/excellent", "excellent"):
        add("good", f"County grades the building quality '{quality}'.")
    elif q in ("low", "low/average"):
        add("warn", f"County grades the building quality '{quality}'.")
    if _norm_label(condition) in ("poor", "fair"):
        add("warn", f"County rates the building condition '{condition}'.")
    labels = {k: label for k, label, _ in CHECKLIST}
    for k, v in checklist.items():
        if v <= 2:
            add("warn", f"Tour: {labels[k]} rated {v}/5.")
    return flags


def clean_checklist(raw: dict[str, Any] | None) -> dict[str, int]:
    out = {}
    for k, v in (raw or {}).items():
        if k in CHECKLIST_KEYS and v not in (None, ""):
            n = int(v)
            if not 1 <= n <= 5:
                raise ValueError(f"{k} must be 1-5")
            out[k] = n
    return out


def construction_score(building: dict[str, Any] | None, listing_year_built: int | None = None,
                       checklist: dict[str, Any] | None = None,
                       photo_analysis: dict[str, Any] | None = None,
                       today: date | None = None) -> dict[str, Any]:
    b = building or {}
    this_year = (today or date.today()).year
    year = b.get("year_built") or listing_year_built
    eff_year = b.get("eff_year")
    constr, quality, condition = b.get("constr_class"), b.get("bldg_quality"), b.get("condition")
    ratings = clean_checklist(checklist)

    parts: dict[str, tuple[float, str]] = {}
    if (qs := QUALITY_SCORES.get(_norm_label(quality))) is not None:
        parts["quality"] = (qs, quality)
    if (cs := CONDITION_SCORES.get(_norm_label(condition))) is not None:
        parts["condition"] = (cs, condition)
    ss, sdetail = _structure(constr, year)
    if ss is not None:
        parts["structure"] = (ss, sdetail)
    basis = max(y for y in (year, eff_year) if y) if (year or eff_year) else None
    if basis:
        age = max(0, this_year - basis)
        parts["age"] = (max(20.0, 100 - age * 1.6), f"{age} years since {'renovation' if basis != year else 'built'} ({basis})")
    if ratings:
        avg = sum(ratings.values()) / len(ratings)
        parts["tour"] = ((avg - 1) / 4 * 100, f"{len(ratings)} of {len(CHECKLIST)} items rated, average {avg:.1f}/5")

    photo = photo_component(photo_analysis, ratings)
    if photo:
        parts["photos"] = (photo[0], photo[1])

    flags = _flags(year, eff_year, constr, b.get("stories"), quality, condition, ratings, this_year)
    for item in (photo_analysis or {}).get("check_on_tour", []):
        prefix = f"Photo {item['photo']}: " if item.get("photo") else "From photos: "
        flags.append({"level": "warn", "text": prefix + item["text"]})
    if not parts:
        return {"score": None, "confidence": None, "components": [], "flags": flags,
                "missing": ["county record or year built", "tour checklist"], "photo_breakdown": None}

    total_w = sum(WEIGHTS[k] for k in parts)
    score = sum(WEIGHTS[k] * v for k, (v, _) in parts.items()) / total_w
    has_county = "quality" in parts or "condition" in parts
    has_tour = len(ratings) >= 5
    confidence = "high" if has_county and has_tour else "medium" if has_county or has_tour else "low"
    missing = []
    if not has_county:
        missing.append("county quality/condition record")
    if not has_tour:
        missing.append("tour checklist (at least 5 items)")
    components = [
        {"key": k, "label": LABELS[k], "score": round(v), "weight": round(WEIGHTS[k] / total_w * 100),
         "detail": d}
        for k, (v, d) in parts.items()
    ]
    return {"score": round(score), "confidence": confidence, "components": components,
            "flags": flags, "missing": missing,
            "photo_breakdown": photo[2] if photo else None}
