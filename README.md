# Rent Pricing Tool

Minimal FastAPI service that returns a rent valuation for a property. Valuations are
computed from a **mock comparable-property dataset**; real data sources come later.

## Project Status

**Phase 6 - Geocoding Abstraction Complete**

### Current Features

- FastAPI API
- Swagger docs
- Valuation engine: filters comparables (distance from coordinates, bedrooms, bathrooms,
  sqft), removes rent outliers (IQR), and reports percentiles and average
- `POST /valuation` backed by the engine, using mock comparable data
- Address-to-coordinate geocoding behind a `Geocoder` interface: a mock geocoder (the
  development default, 13 demo addresses) or the free US Census geocoder (`GEOCODER=census`,
  the Home Assistant app default), with a persistent cache. Coordinates in the request skip it.
- Every successful valuation is saved to a local SQLite database (`data/rentpricingtool.db`,
  created automatically)
- `GET /history` (latest 25 valuations) and `GET /stats` (count and database details)
- Web UI under `/ui` (Jinja2 + Bootstrap 5.3, served locally with no CDN and no Node tooling).
  The valuation form shows the recommended rent, a confidence badge and how the comparables
  were narrowed down, or an "insufficient data" explanation, with a MOCK/RENTCAST data-source
  badge. CSRF-protected and safe to serve through Home Assistant ingress. The history page is
  still a placeholder.
- Packaged as a Home Assistant app (`ha-addon/`): opens from the sidebar through ingress, with no
  published ports and connections accepted only from the Supervisor
- Automated tests

### Roadmap

- RentCast integration
- More geocoding providers (a Nominatim or Geocodio fallback); the US Census geocoder is in

## Requirements

- Python 3.12

## Run

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is then at http://127.0.0.1:8000, with interactive Swagger docs at http://127.0.0.1:8000/docs.

Optional settings (`APP_NAME`, `VERSION`, `ENVIRONMENT`) are read from environment variables;
copy `.env.example` to `.env` and add `--env-file .env` to the `uvicorn` command to use them.

## Home Assistant app

The service can run as a Home Assistant app, shown in the sidebar as **Rent Pricing**
(administrators only) and reachable from the web app, the mobile app and remote access, with no
published ports. Add `https://github.com/cskullerud/rentpricingtool` under **Settings > Apps >
App store > Repositories**, then install **Rent Pricing**. It starts on built-in sample data (no
cost); live RentCast data is a setting. See [`ha-addon/DOCS.md`](ha-addon/DOCS.md) for the
installation steps, the first-run checklist, settings, security and troubleshooting.

The app is built from the `ha-addon/` folder alone, so it contains a copy of `app/`. After
changing anything under `app/`, run `scripts/sync_addon.sh` and commit the result;
`scripts/sync_addon.sh --check` (and the test suite) fails if the copy is out of date.

## Web UI

Open `http://127.0.0.1:8000/ui/`. The UI lives under `/ui`; the JSON API (`/`, `/valuation`,
`/history`, `/stats`) is unchanged. Bootstrap 5.3.8 is vendored in `app/static/vendor/bootstrap`
(see its `VERSION` file), so pages need no internet access. Behind Home Assistant ingress the
`X-Ingress-Path` header is used as the URL prefix for every link and asset.

## How a valuation works

1. Get comparables from the data source (currently a mock dataset).
2. Keep those within 1 mile, +/- 1 bedroom, +/- 1 bathroom and +/- 20% sqft of the subject.
3. Drop rent outliers (below Q1 - 1.5 x IQR or above Q3 + 1.5 x IQR).
4. Report p25, median, p75 and average of what remains. `recommended_rent` is the median,
   and `comparable_count` is the number of comparables used after outlier removal.
5. Report `confidence` (`low` for fewer than 5 comparables, `medium` for 5-9, `high` for 10 or
   more) and a `funnel` showing how many comparables survived each stage: fetched, within the
   distance, matching bedrooms/bathrooms/sqft, after outlier removal, and used. Each valuation
   also logs the funnel as one `valuation_funnel ...` line.

If fewer than `MIN_COMPARABLES_REQUIRED` (default 3) comparables remain after steps 2 and 3,
there is not enough data for a reliable number, so `POST /valuation` returns 404 with
`{"status": "insufficient_data", "detail": "...", "comparable_count": 2, "minimum_required": 3, "funnel": {...}}`
instead of a valuation. The same 404 shape (with `comparable_count` 0) is used when nothing
matches the subject.

## Try it

```bash
curl http://127.0.0.1:8000/
# {"app":"Rent Pricing Tool","status":"online"}

curl -X POST http://127.0.0.1:8000/valuation \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}'
# {"comparable_count":16,"recommended_rent":2512,"p25":2419,"median":2512,"p75":2606,"average":2505,
#  "confidence":"high","funnel":{"comparables_fetched":26,"comparables_after_distance_filter":24,
#  "comparables_after_attribute_filter":18,"comparables_after_outlier_filter":16,"comparables_used":16}}

# Or give the subject's coordinates (both values, or neither); the address is then not geocoded:
curl -X POST http://127.0.0.1:8000/valuation \
  -H "Content-Type: application/json" \
  -d '{"address": "any text", "beds": 3, "baths": 2, "sqft": 1400, "latitude": 32.7678, "longitude": -117.0231}'
```

### Addresses and geocoding

If a request has no `latitude` and `longitude`, the address is geocoded. Set `GEOCODER=census` to
use the free **US Census geocoder** (US addresses only, no key; include the city, state and ZIP
code, for example `123 Main St, San Diego, CA 92101`). Answers are cached in the database for 90
days, apartment/unit/suite/`#number` designators are ignored, and a lookup that fails because the
service is down returns **503** (not "address not found"). Without `GEOCODER` the default is the
**mock geocoder**, which knows 13 fictional addresses around La Mesa and San Diego (for
example `123 Main St, La Mesa, CA`, `456 Palm Ave, La Mesa, CA` and
`789 Broadway, San Diego, CA`); matching ignores case and spacing, and a street alone such
as `123 Main St` works when it is unambiguous. An address it doesn't know returns **404**
with a message; supply `latitude` and `longitude` to value any other location. The mock
comparables are all in La Mesa, so the San Diego addresses geocode but find no comparables
(also a 404).

The geocoder sits behind a `Geocoder` interface in `app/services/geocoding/`; `get_geocoder()`
in the router picks one from `GEOCODER` and is still the dependency tests override.

```bash
curl http://127.0.0.1:8000/history   # latest 25 valuations, newest first
curl http://127.0.0.1:8000/stats     # {"total_valuations":..., "database_path":"...", "database_size_kb":...}
```

## Test

```bash
pytest
```

## Layout

```
app/            FastAPI app: main.py, config.py, schemas.py, routers/valuation.py,
                services/ (statistics, comparables, geo, valuation_engine, data_sources/, geocoding/),
                persistence/ (SQLite database and repository)
tests/          pytest tests
docs/           documentation
scripts/        helper scripts
data/           local data (contents are git-ignored)
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
