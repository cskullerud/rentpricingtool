from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"app": "Rent Pricing Tool", "status": "online"}


def test_valuation_returns_mocked_data():
    response = client.post(
        "/valuation",
        json={"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400},
    )
    assert response.status_code == 200
    assert response.json() == {
        "recommended_rent": 2500,
        "p25": 2300,
        "median": 2500,
        "p75": 2700,
        "confidence": 85,
    }


def test_valuation_rejects_missing_fields():
    response = client.post("/valuation", json={"address": "123 Main St"})
    assert response.status_code == 422
