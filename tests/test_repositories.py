import re

import pytest

from app.persistence import DatabaseManager, PersistenceError, ValuationRepository
from app.schemas import ValuationRequest

RESULT = {
    "comparable_count": 16, "p25": 2419, "median": 2512, "p75": 2606,
    "average": 2505, "recommended_rent": 2512,
}


def subject(address="123 Main St", **overrides):
    return ValuationRequest(address=address, beds=3, baths=2, sqft=1400, **overrides)


def test_save_returns_ids_and_count_tracks_rows(repository):
    assert repository.count_valuations() == 0
    first = repository.save_valuation_request(subject(), RESULT)
    second = repository.save_valuation_request(subject(), RESULT)
    assert (first, second) == (1, 2)
    assert repository.count_valuations() == 2


def test_stores_the_request_and_the_result(repository):
    repository.save_valuation_request(
        subject(latitude=32.7678, longitude=-117.0231), RESULT, created_at="2026-10-03T12:00:00+00:00"
    )
    (row,) = repository.get_recent_valuations()
    assert row == {
        "id": 1, "created_at": "2026-10-03T12:00:00+00:00",
        "address": "123 Main St", "beds": 3, "baths": 2.0, "sqft": 1400,
        "latitude": 32.7678, "longitude": -117.0231,
        "comparable_count": 16, "p25": 2419.0, "median": 2512.0, "p75": 2606.0,
        "average": 2505.0, "recommended_rent": 2512.0,
    }


def test_missing_coordinates_are_stored_as_null(repository):
    repository.save_valuation_request(subject(), RESULT)
    (row,) = repository.get_recent_valuations()
    assert row["latitude"] is None and row["longitude"] is None


def test_created_at_defaults_to_now_in_iso_8601_utc(repository):
    repository.save_valuation_request(subject(), RESULT)
    (row,) = repository.get_recent_valuations()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", row["created_at"])


def test_recent_valuations_are_newest_first(repository):
    for n in range(5):
        repository.save_valuation_request(subject(address=f"{n} Test St"), RESULT)
    assert [r["address"] for r in repository.get_recent_valuations()] == [
        "4 Test St", "3 Test St", "2 Test St", "1 Test St", "0 Test St",
    ]


def test_recent_valuations_respects_the_limit_and_defaults_to_25(repository):
    for n in range(30):
        repository.save_valuation_request(subject(address=f"{n} Test St"), RESULT)
    assert len(repository.get_recent_valuations(limit=3)) == 3
    assert len(repository.get_recent_valuations()) == 25
    assert repository.count_valuations() == 30


def test_recent_valuations_rejects_a_bad_limit(repository):
    with pytest.raises(ValueError):
        repository.get_recent_valuations(limit=0)


def test_values_are_bound_as_parameters_not_pasted_into_sql(repository):
    nasty = "x'); DROP TABLE valuation_requests; --"
    repository.save_valuation_request(subject(address=nasty), RESULT)
    assert repository.count_valuations() == 1
    assert repository.get_recent_valuations()[0]["address"] == nasty


def test_data_persists_across_repository_instances(tmp_path):
    path = tmp_path / "shared.db"
    ValuationRepository(DatabaseManager(path)).save_valuation_request(subject(), RESULT)
    assert ValuationRepository(DatabaseManager(path)).count_valuations() == 1


def test_database_details(repository):
    repository.save_valuation_request(subject(), RESULT)
    assert repository.database_path.endswith("test.db")
    assert repository.database_size_bytes() > 0


def test_database_errors_become_persistence_errors(tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("not a folder")
    broken = ValuationRepository(DatabaseManager(blocker / "db.sqlite"))
    with pytest.raises(PersistenceError):
        broken.count_valuations()
    with pytest.raises(PersistenceError):
        broken.save_valuation_request(subject(), RESULT)
