"""Settings for the apartment finder. Values come from environment variables
(optionally loaded from a .env file in the project root)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, no overriding existing env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class MustHaves:
    bedrooms: int = 1
    max_rent: int = field(default_factory=lambda: int(os.environ.get("APTFINDER_MAX_RENT", "3500")))
    in_unit_laundry: bool = True
    gym: bool = True
    covered_parking: bool = True


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("APTFINDER_DB", ROOT / "data" / "aptfinder.db"))
    )
    work_address: str = field(
        default_factory=lambda: os.environ.get(
            "APTFINDER_WORK_ADDRESS", "Monte Villa Pkwy, Bothell, WA 98021"
        )
    )
    # Work hours 9-5: leave home 8:15 to be in by 9, leave work at 5.
    depart_home: str = "08:15"
    depart_work: str = "17:00"
    timezone: str = "America/Los_Angeles"
    workdays_per_month: int = 21
    # Fuel cost per mile (≈ $4.50/gal at 28 mpg). Override with APTFINDER_COST_PER_MILE.
    cost_per_mile: float = field(
        default_factory=lambda: float(os.environ.get("APTFINDER_COST_PER_MILE", "0.16"))
    )
    claude_model: str = field(
        default_factory=lambda: os.environ.get("APTFINDER_CLAUDE_MODEL", "claude-opus-5-5")
    )
    anthropic_api_key: str | None = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY"))
    google_maps_api_key: str | None = field(
        default_factory=lambda: os.environ.get("GOOGLE_MAPS_API_KEY")
    )
    must_haves: MustHaves = field(default_factory=MustHaves)


def get_settings() -> Settings:
    return Settings()
