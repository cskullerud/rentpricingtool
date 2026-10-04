from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SUBJECT = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"app": "Rent Pricing Tool", "status": "online"}


def test_valuation_returns_engine_result():
    response = client.post("/valuation", json=SUBJECT)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"comparable_count", "p25", "median", "p75", "average", "recommended_rent"}
    assert body["comparable_count"] > 0
    assert body["recommended_rent"] == body["median"]


def test_valuation_varies_with_the_subject():
    other = {"address": "1 Small St", "beds": 2, "baths": 1, "sqft": 1150}
    assert client.post("/valuation", json=other).json() != client.post("/valuation", json=SUBJECT).json()


def test_valuation_with_no_comparables_returns_404():
    response = client.post(
        "/valuation", json={"address": "1 Castle Rd", "beds": 10, "baths": 8, "sqft": 9000}
    )
    assert response.status_code == 404


def test_valuation_rejects_missing_fields():
    response = client.post("/valuation", json={"address": "123 Main St"})
    assert response.status_code == 422
