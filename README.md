# Rent Pricing Tool

Minimal FastAPI service that returns a rent valuation for a property. Valuations are
computed from a **mock comparable-property dataset**; real data sources come later.

## Project Status

**Phase 2 - Valuation Engine (mock comparables) Complete**

### Current Features

- FastAPI API
- Swagger docs
- Valuation engine: filters comparables (distance, bedrooms, bathrooms, sqft), removes
  rent outliers (IQR), and reports percentiles and average
- `POST /valuation` backed by the engine, using mock comparable data
- Automated tests

### Roadmap

- RentCast integration
- Geocoding
- Persistence layer

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

## How a valuation works

1. Get comparables from the data source (currently a mock dataset).
2. Keep those within 1 mile, +/- 1 bedroom, +/- 1 bathroom and +/- 20% sqft of the subject.
3. Drop rent outliers (below Q1 - 1.5 x IQR or above Q3 + 1.5 x IQR).
4. Report p25, median, p75 and average of what remains. `recommended_rent` is the median,
   and `comparable_count` is the number of comparables used after outlier removal.

If nothing matches the subject, `POST /valuation` returns 404.

## Try it

```bash
curl http://127.0.0.1:8000/
# {"app":"Rent Pricing Tool","status":"online"}

curl -X POST http://127.0.0.1:8000/valuation \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}'
# {"comparable_count":16,"recommended_rent":2512,"p25":2419,"median":2512,"p75":2606,"average":2505}
```

## Test

```bash
pytest
```

## Layout

```
app/            FastAPI app: main.py, config.py, schemas.py, routers/valuation.py,
                services/ (statistics, comparables, valuation_engine, data_sources/)
tests/          pytest tests
docs/           documentation
scripts/        helper scripts
data/           local data (contents are git-ignored)
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
