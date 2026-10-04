import os

from fastapi.testclient import TestClient

from app.main import app
from app.persistence import DatabaseManager, ValuationRepository, get_repository

client = TestClient(app)

SUBJECT = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}


def test_valuation_response_format_is_unchanged():
    body = client.post("/valuation", json=SUBJECT).json()
    assert body == {
        "comparable_count": 16, "recommended_rent": 2512, "p25": 2419,
        "median": 2512, "p75": 2606, "average": 2505,
    }


def test_each_successful_valuation_is_saved():
    client.post("/valuation", json=SUBJECT)
    client.post("/valuation", json={**SUBJECT, "address": "9 Other St", "beds": 2, "baths": 1, "sqft": 1150})
    assert client.get("/stats").json()["total_valuations"] == 2


def test_failed_valuations_are_not_saved():
    client.post("/valuation", json={**SUBJECT, "beds": 10, "baths": 8, "sqft": 9000})  # 404
    client.post("/valuation", json={"address": "x"})  # 422
    assert client.get("/stats").json()["total_valuations"] == 0


def test_stats_on_an_empty_database(repository):
    body = client.get("/stats").json()
    assert body["total_valuations"] == 0
    assert body["database_path"] == repository.database_path
    assert set(body) == {"total_valuations", "database_path", "database_size_kb"}


def test_stats_reports_the_database_size_in_kb(repository):
    for _ in range(3):
        client.post("/valuation", json=SUBJECT)
    body = client.get("/stats").json()
    assert body["total_valuations"] == 3
    assert body["database_size_kb"] == os.path.getsize(repository.database_path) // 1024
    assert isinstance(body["database_size_kb"], int)


def test_history_is_empty_at_first():
    response = client.get("/history")
    assert response.status_code == 200
    assert response.json() == []


def test_history_returns_request_and_response_data_newest_first():
    client.post("/valuation", json=SUBJECT)
    client.post(
        "/valuation",
        json={"address": "9 Other St", "beds": 2, "baths": 1, "sqft": 1150, "latitude": 32.7678, "longitude": -117.0231},
    )
    history = client.get("/history").json()
    assert [h["address"] for h in history] == ["9 Other St", "123 Main St"]
    newest, oldest = history
    assert set(newest) == {
        "id", "created_at", "address", "beds", "baths", "sqft", "latitude", "longitude",
        "comparable_count", "p25", "median", "p75", "average", "recommended_rent",
    }
    assert newest["latitude"] == 32.7678 and oldest["latitude"] is None
    assert oldest["recommended_rent"] == 2512 and oldest["comparable_count"] == 16
    assert oldest["created_at"] <= newest["created_at"]


def test_history_is_limited_to_the_latest_25(repository):
    for n in range(30):
        client.post("/valuation", json={**SUBJECT, "address": f"{n} Test St"})
    history = client.get("/history").json()
    assert len(history) == 25
    assert history[0]["address"] == "29 Test St"
    assert history[-1]["address"] == "5 Test St"
    assert client.get("/stats").json()["total_valuations"] == 30


def test_valuation_still_works_when_the_database_is_unusable(tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("not a folder")
    broken = ValuationRepository(DatabaseManager(blocker / "db.sqlite"))
    app.dependency_overrides[get_repository] = lambda: broken
    response = client.post("/valuation", json=SUBJECT)
    assert response.status_code == 200
    assert response.json()["recommended_rent"] == 2512


def test_stats_and_history_report_503_when_the_database_is_unusable(tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("not a folder")
    broken = ValuationRepository(DatabaseManager(blocker / "db.sqlite"))
    app.dependency_overrides[get_repository] = lambda: broken
    assert client.get("/stats").status_code == 503
    assert client.get("/history").status_code == 503
