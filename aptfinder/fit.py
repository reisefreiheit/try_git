"""Must-have checks and true monthly / move-in cost for a listing."""

from __future__ import annotations

from typing import Any

from .config import MustHaves


def _status(value: bool | None) -> str:
    return "unknown" if value is None else ("pass" if value else "fail")


def must_have_checks(listing: dict[str, Any], must: MustHaves) -> list[dict[str, str]]:
    beds, rent = listing.get("bedrooms"), listing.get("rent")
    return [
        {"label": f"{must.bedrooms}+ bedroom",
         "status": _status(None if beds is None else beds >= must.bedrooms)},
        {"label": f"Rent under ${must.max_rent:,}",
         "status": _status(None if rent is None else rent < must.max_rent)},
        {"label": "In-unit laundry", "status": _status(listing.get("in_unit_laundry"))},
        {"label": "Gym in building", "status": _status(listing.get("gym"))},
        {"label": "Covered parking", "status": _status(listing.get("covered_parking"))},
    ]


def costs(listing: dict[str, Any], commute: dict[str, Any] | None) -> dict[str, Any]:
    rent = listing.get("rent") or 0
    lines = {
        "rent": rent,
        "parking": listing.get("parking_cost") or 0,
        "utilities": listing.get("utilities_estimate") or 0,
        "fees": listing.get("monthly_fees") or 0,
        "commute": (commute or {}).get("monthly_cost") or 0,
    }
    unknown = [k for k, src in (("parking", "parking_cost"), ("utilities", "utilities_estimate"),
                                ("fees", "monthly_fees")) if listing.get(src) is None]
    if not commute:
        unknown.append("commute")
    move_in = {
        "first_month": rent,
        "deposit": listing.get("deposit") or 0,
        "fees": listing.get("move_in_fees") or 0,
    }
    return {
        "monthly": lines,
        "monthly_total": round(sum(lines.values()), 2),
        "monthly_unknown": unknown,
        "move_in": move_in,
        "move_in_total": round(sum(move_in.values()), 2),
    }
