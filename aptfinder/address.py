"""Street address normalization so listing addresses can be matched to county records."""

from __future__ import annotations

import re

DIRECTIONS = {
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
}
SUFFIXES = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "PLACE": "PL", "BOULEVARD": "BLVD",
    "DRIVE": "DR", "ROAD": "RD", "COURT": "CT", "LANE": "LN", "PARKWAY": "PKWY",
    "PKY": "PKWY", "CIRCLE": "CIR", "TERRACE": "TER", "HIGHWAY": "HWY", "WAY": "WAY",
}
UNIT_RE = re.compile(r"(\b(APT|APARTMENT|UNIT|STE|SUITE|BLDG|BUILDING)\b|#)\s*[A-Z0-9-]+\b")


def normalize_address(addr: str | None) -> str:
    """'12345 Northeast 85th Street, Unit 210, Kirkland' -> '12345 NE 85TH ST'."""
    if not addr:
        return ""
    s = addr.upper().split(",")[0]
    s = UNIT_RE.sub(" ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    words = [DIRECTIONS.get(w, SUFFIXES.get(w, w)) for w in s.split()]
    return " ".join(words)


def house_number(addr_norm: str) -> str | None:
    first = addr_norm.split(" ", 1)[0] if addr_norm else ""
    return first if first.isdigit() else None
