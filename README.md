# Eastside Apartment Finder

A personal tool for an apartment search on the Seattle Eastside (Kirkland, Bellevue,
Redmond, Bothell, Woodinville, Kenmore). It runs on your own computer.

- **Paste a listing** from any site; Claude pulls out the rent, size, fees, parking,
  laundry, gym and any warning signs.
- **Car commute to IonQ (Monte Villa Pkwy, Bothell)**: predicted-traffic drive times
  leaving home at 8:15 and work at 5:00, with and without the I-405 express toll lanes.
- **True monthly cost**: rent + parking + utilities + fees + gas and tolls, plus the
  total due at move-in.
- **Must-haves** checked on every listing: 1 bedroom, rent under $3,000, in-unit laundry,
  gym in building, covered parking.
- **Construction-quality score (0-100)** with a confidence level and plain-language
  flags, built from King County Assessor records, building-era patterns and your own
  tour checklist.
- **Tracking**: interested → contacted → tour scheduled → toured → applied → rejected / signed.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then add your API keys (see below)
uvicorn aptfinder.main:create_app --factory
```

Open http://127.0.0.1:8000. Data is stored in `data/aptfinder.db`.

### API keys

Both are optional: without them you can still enter details by hand.

| Key | Used for | How to get it | Expected cost |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Reading pasted listings | console.anthropic.com → API keys | A few cents per listing |
| `GOOGLE_MAPS_API_KEY` | Drive times and tolls | Google Cloud console → enable **Routes API** → create an API key | Likely $0 within Google's monthly free usage for a personal search; each listing makes 4 requests and results are cached |

To stay inside a $50 budget, set a monthly spending limit in the Anthropic console and a
budget alert or quota in Google Cloud.

## County building records (for the construction score)

1. Go to the King County Assessor **Data Download** page.
2. Download **Apartment Complex** and **Condo Complex and Units** (zip files).
3. Import them:

```bash
python -m aptfinder.assessor apartment ~/Downloads/"Apartment Complex.zip"
python -m aptfinder.assessor condo ~/Downloads/"Condo Complex and Units.zip"
```

Then open a listing → **Construction** → **Find in county records**, and pick the match.
Apartment complexes are often recorded under one main address, so if nothing matches,
look the building up on King County's eReal Property site and enter the details by hand.
North Bothell and Mill Creek are in Snohomish County; enter those by hand for now.

The county's numeric codes are decoded with the tables in `aptfinder/assessor.py`. If a
decoded value looks wrong compared with the county's property page, adjust those tables.

## How the construction score works

| Factor | Weight | Source |
|---|---|---|
| Building quality grade | 30 | County record |
| Condition rating | 15 | County record |
| Construction type (steel/concrete/wood/masonry) | 10 | County record |
| Age, using the renovation year if there is one | 15 | County record or listing |
| Tour checklist (9 items rated 1-5) | 30 | You, during the tour |

Missing factors are left out and the remaining weights are scaled up. Confidence is
**high** with county grades plus at least 5 tour ratings, **medium** with one of those,
and **low** otherwise.

Flags call out building-era issues worth asking about: lead paint (pre-1978), aluminum
wiring (1965-73), polybutylene pipes (1978-95), the 1985-2005 "leaky building" era for
wood-frame buildings, noise in newer 5+ story wood-over-concrete buildings, older masonry,
and buildings under 12 years old, which are exempt from Washington's rent-increase cap
(HB 1217).

This is a screening estimate, not an inspection.

## Settings

Defaults are in `aptfinder/config.py`: work address, departure times, workdays per month,
fuel cost per mile, the Claude model and must-haves. The work address, fuel cost, model and
database location can also be set in `.env`.

## Tests

```bash
pytest
```

## Roadmap

- Construction score v2: listing-photo analysis, pasted resident reviews, city permit
  history, liquefaction zones.
- Map with drive-time zones from IonQ; weighted overall score; side-by-side comparison.
- Import saved-search alert emails.
