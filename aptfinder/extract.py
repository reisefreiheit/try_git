"""Turn pasted listing text into structured fields with the Claude API."""

from __future__ import annotations

import json
from typing import Any

import anthropic

from .config import Settings


def _nullable(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


FIELDS: dict[str, dict] = {
    "title": _nullable("string"),
    "address": _nullable("string"),
    "city": _nullable("string"),
    "unit": _nullable("string"),
    "rent": _nullable("number"),
    "bedrooms": _nullable("number"),
    "bathrooms": _nullable("number"),
    "sqft": _nullable("number"),
    "year_built": _nullable("integer"),
    "in_unit_laundry": _nullable("boolean"),
    "gym": _nullable("boolean"),
    "covered_parking": _nullable("boolean"),
    "parking_cost": _nullable("number"),
    "parking_type": _nullable("string"),
    "pet_policy": _nullable("string"),
    "utilities_estimate": _nullable("number"),
    "monthly_fees": _nullable("number"),
    "deposit": _nullable("number"),
    "move_in_fees": _nullable("number"),
    "available_date": _nullable("string"),
    "red_flags": {"type": "array", "items": {"type": "string"}},
}

SCHEMA = {
    "type": "object",
    "properties": FIELDS,
    "required": list(FIELDS),
    "additionalProperties": False,
}

SYSTEM = """You extract facts from rental listings in the Seattle Eastside \
(Kirkland, Bellevue, Redmond, Bothell, Woodinville, Kenmore and nearby).

Rules:
- Use only what the listing states. If a fact is not stated, return null. Do not guess.
- address: street address only (e.g. "12345 NE 85th St"), without the unit number. city: city name.
- rent: the monthly base rent in dollars. If a range is given, use the lowest rent for the unit described.
- in_unit_laundry: true only for a washer/dryer inside the unit; false if laundry is shared or on-site only.
- gym: true if the building or complex has a fitness center/gym.
- covered_parking: true for a garage, carport or covered/underground parking; false if only surface \
or street parking is mentioned.
- parking_cost, utilities_estimate, monthly_fees: monthly dollar amounts. monthly_fees covers recurring \
amenity, admin, trash, pest, tech or similar fees beyond rent, parking and utilities.
- deposit: refundable security deposit. move_in_fees: non-refundable one-time fees \
(admin, move-in, application) added together.
- red_flags: short phrases for scam or quality warning signs actually present in the text, such as a \
request to wire money, an owner who is abroad, rent far below market, pressure to pay before touring, \
or mentions of mold, leaks or construction noise. Empty list if none."""


class ExtractionError(RuntimeError):
    pass


def extract_listing(text: str, settings: Settings, client: Any | None = None) -> dict[str, Any]:
    """Return a dict with every key in FIELDS. Raises ExtractionError on failure."""
    if not text.strip():
        raise ExtractionError("Listing text is empty.")
    if client is None:
        if not settings.anthropic_api_key:
            raise ExtractionError("ANTHROPIC_API_KEY is not set; enter the details by hand instead.")
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.beta.messages.create(
            model=settings.claude_model,
            max_tokens=16000,
            system=SYSTEM,
            # Extraction is simple; low effort keeps cost to a few cents per listing.
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            # If a request is declined by a safety classifier, retry on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": f"<listing>\n{text}\n</listing>"}],
        )
    except anthropic.AuthenticationError as e:
        raise ExtractionError("Anthropic API key was rejected.") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Rate limited by the Anthropic API; try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Could not reach the Anthropic API.") from e

    if response.stop_reason == "refusal":
        raise ExtractionError("The model declined to process this text.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The response was cut off; try pasting less text.")

    text_out = next((b.text for b in response.content if b.type == "text"), None)
    if not text_out:
        raise ExtractionError("No result returned.")
    try:
        data = json.loads(text_out)
    except json.JSONDecodeError as e:
        raise ExtractionError("Result was not valid JSON.") from e
    return {k: data.get(k) for k in FIELDS} | {"red_flags": data.get("red_flags") or []}
