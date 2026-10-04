# Rent Pricing Tool

Minimal FastAPI service that returns a rent valuation for a property. The valuation is
currently **mocked**; real pricing logic comes later.

## Project Status

**Phase 1 - Scaffold Complete**

### Current Features

- FastAPI API
- Swagger docs
- Mock valuation endpoint
- Automated tests

### Roadmap

- Real valuation engine
- Comparable filtering
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

## Try it

```bash
curl http://127.0.0.1:8000/
# {"app":"Rent Pricing Tool","status":"online"}

curl -X POST http://127.0.0.1:8000/valuation \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}'
# {"recommended_rent":2500,"p25":2300,"median":2500,"p75":2700,"confidence":85}
```

## Test

```bash
pytest
```

## Layout

```
app/            FastAPI app: main.py, config.py, schemas.py, routers/valuation.py
tests/          pytest tests
docs/           documentation
scripts/        helper scripts
data/           local data (contents are git-ignored)
```
