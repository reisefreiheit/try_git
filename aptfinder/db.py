"""SQLite storage. Small and local: one file, plain SQL, no ORM."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATUSES = ["interested", "contacted", "tour_scheduled", "toured", "applied", "rejected", "signed"]

# Listing columns the API may set directly. JSON columns are handled separately.
LISTING_FIELDS = [
    "status", "url", "title", "address", "city", "unit",
    "rent", "bedrooms", "bathrooms", "sqft", "year_built",
    "in_unit_laundry", "gym", "covered_parking", "parking_cost", "parking_type",
    "pet_policy", "utilities_estimate", "monthly_fees", "deposit", "move_in_fees",
    "available_date", "notes",
]
BOOL_FIELDS = {"in_unit_laundry", "gym", "covered_parking"}
JSON_FIELDS = {"commute_json", "building_json", "checklist_json", "extracted_json", "photos_json"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'interested',
    url TEXT,
    source_text TEXT,
    title TEXT,
    address TEXT,
    city TEXT,
    unit TEXT,
    rent REAL,
    bedrooms REAL,
    bathrooms REAL,
    sqft REAL,
    year_built INTEGER,
    in_unit_laundry INTEGER,
    gym INTEGER,
    covered_parking INTEGER,
    parking_cost REAL,
    parking_type TEXT,
    pet_policy TEXT,
    utilities_estimate REAL,
    monthly_fees REAL,
    deposit REAL,
    move_in_fees REAL,
    available_date TEXT,
    notes TEXT,
    extracted_json TEXT,
    commute_json TEXT,
    building_json TEXT,
    checklist_json TEXT,
    photos_json TEXT
);

CREATE TABLE IF NOT EXISTS assessor_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    county TEXT NOT NULL,
    kind TEXT NOT NULL,
    parcel TEXT,
    name TEXT,
    address TEXT,
    address_norm TEXT,
    zip TEXT,
    year_built INTEGER,
    eff_year INTEGER,
    stories INTEGER,
    units INTEGER,
    constr_class TEXT,
    bldg_quality TEXT,
    condition TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_assessor_addr ON assessor_records(address_norm);

CREATE TABLE IF NOT EXISTS route_cache (
    key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(listings)")}
        for col in ("photos_json",):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE listings ADD COLUMN {col} TEXT")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---- listings ---------------------------------------------------------

    def _row_to_listing(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for f in BOOL_FIELDS:
            if d.get(f) is not None:
                d[f] = bool(d[f])
        for f in JSON_FIELDS:
            raw = d.pop(f, None)
            d[f.removesuffix("_json")] = json.loads(raw) if raw else None
        return d

    @staticmethod
    def _encode(field: str, value: Any) -> Any:
        if field in BOOL_FIELDS and value is not None:
            return int(bool(value))
        return value

    def create_listing(self, fields: dict[str, Any], source_text: str | None = None,
                       extracted: dict | None = None) -> dict[str, Any]:
        ts = now_iso()
        data = {k: self._encode(k, v) for k, v in fields.items() if k in LISTING_FIELDS}
        data.setdefault("status", "interested")
        data.update(created_at=ts, updated_at=ts, source_text=source_text,
                    extracted_json=json.dumps(extracted) if extracted else None)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO listings ({cols}) VALUES ({marks})", list(data.values()))
            new_id = cur.lastrowid
        return self.get_listing(new_id)

    def get_listing(self, listing_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        return self._row_to_listing(row) if row else None

    def list_listings(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM listings ORDER BY updated_at DESC").fetchall()
        return [self._row_to_listing(r) for r in rows]

    def update_listing(self, listing_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        data = {k: self._encode(k, v) for k, v in fields.items() if k in LISTING_FIELDS}
        if "status" in data and data["status"] not in STATUSES:
            raise ValueError(f"unknown status {data['status']!r}")
        if data:
            data["updated_at"] = now_iso()
            sets = ", ".join(f"{k} = ?" for k in data)
            with self.tx() as c:
                c.execute(f"UPDATE listings SET {sets} WHERE id = ?", [*data.values(), listing_id])
        return self.get_listing(listing_id)

    def set_listing_json(self, listing_id: int, field: str, value: Any) -> dict[str, Any] | None:
        if field not in JSON_FIELDS:
            raise ValueError(field)
        with self.tx() as c:
            c.execute(
                f"UPDATE listings SET {field} = ?, updated_at = ? WHERE id = ?",
                (json.dumps(value) if value is not None else None, now_iso(), listing_id),
            )
        return self.get_listing(listing_id)

    def delete_listing(self, listing_id: int) -> bool:
        with self.tx() as c:
            cur = c.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
        return cur.rowcount > 0

    # ---- assessor records -------------------------------------------------

    def replace_assessor_records(self, county: str, kind: str, records: list[dict[str, Any]]) -> int:
        cols = ["county", "kind", "parcel", "name", "address", "address_norm", "zip",
                "year_built", "eff_year", "stories", "units", "constr_class",
                "bldg_quality", "condition", "raw_json"]
        with self.tx() as c:
            c.execute("DELETE FROM assessor_records WHERE county = ? AND kind = ?", (county, kind))
            c.executemany(
                f"INSERT INTO assessor_records ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [[county, kind, *[r.get(k) for k in cols[2:]]] for r in records],
            )
        return len(records)

    def assessor_candidates(self, house_number: str) -> list[dict[str, Any]]:
        """Records whose normalized address starts with the same house number."""
        rows = self._conn.execute(
            "SELECT * FROM assessor_records WHERE address_norm LIKE ?", (f"{house_number} %",)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_assessor_record(self, record_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM assessor_records WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row else None

    def assessor_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM assessor_records").fetchone()[0]

    # ---- route cache ------------------------------------------------------

    def cache_get(self, key: str) -> dict | None:
        row = self._conn.execute("SELECT result_json FROM route_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key: str, value: dict) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO route_cache (key, created_at, result_json) VALUES (?, ?, ?)",
                (key, now_iso(), json.dumps(value)),
            )
