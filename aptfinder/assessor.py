"""Import King County Assessor building records and match them to listings.

Download from the King County Assessor "Data Download" page:
  - "Apartment Complex"           -> EXTR_AptComplex.csv
  - "Condo Complex and Units"     -> EXTR_CondoComplex.csv
Then run, for example:
  python -m aptfinder.assessor apartment ~/Downloads/Apartment\\ Complex.zip
  python -m aptfinder.assessor condo ~/Downloads/Condo\\ Complex\\ and\\ Units.zip

Column names are matched loosely, so small changes in the county's export
do not break the import. Numeric codes are decoded with the tables below.
The raw codes are kept so you can check them against the county's own
property page (King County eReal Property).
"""

from __future__ import annotations

import csv
import difflib
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .address import house_number, normalize_address
from .db import Database

# Candidate header names for each field (compared lowercase, letters/digits only).
COLUMNS = {
    "major": ["major"],
    "minor": ["minor"],
    "name": ["complexdescr", "complexname", "name", "descr"],
    "address": ["address", "situsaddress", "addr"],
    "zip": ["zipcode", "zip"],
    "year_built": ["yrbuilt", "yearbuilt"],
    "eff_year": ["effyr", "effyear", "effectiveyear"],
    "stories": ["nbrstories", "stories"],
    "units": ["nbrunits", "units"],
    "constr_class": ["constrclass", "constructionclass"],
    "bldg_quality": ["bldgquality", "buildingquality", "quality"],
    "condition": ["condition"],
}
REQUIRED = ["address", "year_built"]

# King County commercial/apartment code tables. Verify against EXTR_LookUp.csv
# if a decoded value looks wrong; text values in the export pass through as-is.
CONSTR_CLASS = {"1": "Structural steel", "2": "Reinforced concrete", "3": "Masonry",
                "4": "Wood frame", "5": "Prefab steel"}
QUALITY = {"1": "Low", "2": "Low/Average", "3": "Average", "4": "Average/Good",
           "5": "Good", "6": "Good/Excellent", "7": "Excellent"}
CONDITION = {"1": "Poor", "2": "Fair", "3": "Average", "4": "Good", "5": "Excellent"}


class ImportError_(RuntimeError):
    pass


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.lower())


def _int(v: Any) -> int | None:
    try:
        n = int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None
    return n or None  # county exports use 0 for "unknown"


def _decode(value: Any, table: dict[str, str]) -> str | None:
    s = str(value or "").strip()
    if not s or s == "0":
        return None
    code = s.split(".")[0]
    return table.get(code, s.title())


def parse_rows(rows: Iterable[dict[str, str]], headers: list[str]) -> list[dict[str, Any]]:
    by_key = {_key(h): h for h in headers}
    col = {}
    for field, names in COLUMNS.items():
        col[field] = next((by_key[n] for n in names if n in by_key), None)
    missing = [f for f in REQUIRED if not col[f]]
    if missing:
        raise ImportError_(
            f"Could not find column(s) {missing} in file. Columns present: {', '.join(headers)}"
        )

    def get(row: dict, field: str) -> str:
        return (row.get(col[field]) or "").strip() if col[field] else ""

    out = []
    for row in rows:
        addr = " ".join(get(row, "address").split())
        if not addr:
            continue
        major, minor = get(row, "major"), get(row, "minor")
        out.append({
            "parcel": f"{major.zfill(6)}{minor.zfill(4)}" if major and minor else (major or None),
            "name": get(row, "name").title() or None,
            "address": addr,
            "address_norm": normalize_address(addr),
            "zip": get(row, "zip")[:5] or None,
            "year_built": _int(get(row, "year_built")),
            "eff_year": _int(get(row, "eff_year")),
            "stories": _int(get(row, "stories")),
            "units": _int(get(row, "units")),
            "constr_class": _decode(get(row, "constr_class"), CONSTR_CLASS),
            "bldg_quality": _decode(get(row, "bldg_quality"), QUALITY),
            "condition": _decode(get(row, "condition"), CONDITION),
            "raw_json": json.dumps({k: row.get(v) for k, v in col.items() if v}),
        })
    return out


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read a CSV, or the first matching CSV inside a .zip."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            preferred = [n for n in names if "complex" in n.lower()] or names
            if not preferred:
                raise ImportError_(f"No CSV file found in {path.name}")
            raw = zf.read(preferred[0])
    else:
        raw = path.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace") if raw[:3] == b"\xef\xbb\xbf" else raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return rows, list(reader.fieldnames or [])


def import_file(db: Database, kind: str, path: Path, county: str = "king") -> int:
    rows, headers = read_csv(path)
    records = parse_rows(rows, headers)
    return db.replace_assessor_records(county, kind, records)


def match_candidates(db: Database, address: str, limit: int = 5) -> list[dict[str, Any]]:
    """County records most likely to be the listing's building, best first."""
    norm = normalize_address(address)
    num = house_number(norm)
    if not num:
        return []
    scored = []
    for rec in db.assessor_candidates(num):
        ratio = difflib.SequenceMatcher(None, norm, rec["address_norm"]).ratio()
        if ratio >= 0.6:
            scored.append((ratio, rec))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [{**rec, "match_score": round(r, 2)} for r, rec in scored[:limit]]


def record_to_building(rec: dict[str, Any]) -> dict[str, Any]:
    """Shape stored on a listing as its building data."""
    return {
        "source": f"{rec['county']}_county_assessor",
        "assessor_id": rec["id"],
        "parcel": rec.get("parcel"),
        "name": rec.get("name"),
        "address": rec.get("address"),
        "year_built": rec.get("year_built"),
        "eff_year": rec.get("eff_year"),
        "stories": rec.get("stories"),
        "units": rec.get("units"),
        "constr_class": rec.get("constr_class"),
        "bldg_quality": rec.get("bldg_quality"),
        "condition": rec.get("condition"),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("apartment", "condo"):
        print("usage: python -m aptfinder.assessor {apartment|condo} PATH_TO_CSV_OR_ZIP", file=sys.stderr)
        return 2
    from .config import get_settings

    db = Database(get_settings().db_path)
    try:
        n = import_file(db, argv[0], Path(argv[1]).expanduser())
    except (ImportError_, OSError, zipfile.BadZipFile) as e:
        print(f"Import failed: {e}", file=sys.stderr)
        return 1
    print(f"Imported {n} {argv[0]} records from King County.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
