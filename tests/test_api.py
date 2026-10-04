from fastapi.testclient import TestClient

from app.config import DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE
from app.main import app

client = TestClient(app)

SUBJECT = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}
# Pins a request to the mock default point, so tests that use made-up addresses don't
# depend on the geocoder.
AT_DEFAULT_POINT = {"latitude": DEFAULT_SUBJECT_LATITUDE, "longitude": DEFAULT_SUBJECT_LONGITUDE}


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
    other = {"address": "1 Small St", "beds": 2, "baths": 1, "sqft": 1150, **AT_DEFAULT_POINT}
    other_response = client.post("/valuation", json=other)
    subject_response = client.post("/valuation", json=SUBJECT)
    assert other_response.status_code == 200 and subject_response.status_code == 200
    assert other_response.json() != subject_response.json()


def test_valuation_with_no_comparables_returns_404():
    response = client.post(
        "/valuation",
        json={"address": "1 Castle Rd", "beds": 10, "baths": 8, "sqft": 9000, **AT_DEFAULT_POINT},
    )
    assert response.status_code == 404
    assert "comparable" in response.json()["detail"]


def test_valuation_rejects_missing_fields():
    response = client.post("/valuation", json={"address": "123 Main St"})
    assert response.status_code == 422


def test_valuation_without_coordinates_geocodes_the_address():
    default = client.post("/valuation", json=SUBJECT).json()
    from app.config import DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE

    explicit = client.post(
        "/valuation",
        json={**SUBJECT, "latitude": DEFAULT_SUBJECT_LATITUDE, "longitude": DEFAULT_SUBJECT_LONGITUDE},
    ).json()
    assert explicit == default


def test_valuation_far_from_all_comparables_returns_404():
    new_york = {**SUBJECT, "latitude": 40.7128, "longitude": -74.0060}
    response = client.post("/valuation", json=new_york)
    assert response.status_code == 404
    assert "comparable" in response.json()["detail"]


def test_valuation_rejects_a_lone_coordinate():
    assert client.post("/valuation", json={**SUBJECT, "latitude": 32.77}).status_code == 422
    assert client.post("/valuation", json={**SUBJECT, "longitude": -117.02}).status_code == 422


def test_valuation_rejects_out_of_range_coordinates():
    assert client.post("/valuation", json={**SUBJECT, "latitude": 91, "longitude": 0}).status_code == 422
    assert client.post("/valuation", json={**SUBJECT, "latitude": 0, "longitude": 181}).status_code == 422
