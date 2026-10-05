import pytest
from fastapi.testclient import TestClient

import app.config as config
from app.config import DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE
from app.main import app
from app.persistence import ValuationRepository
from app.routers.valuation import get_comparable_source
from app.schemas import ValuationRequest
from app.services.data_sources import ComparableDataSource
from app.services.valuation_engine import InsufficientDataError, NoComparablesError, run_valuation

SUBJECT = ValuationRequest(address="123 Main St", beds=3, baths=2, sqft=1400)
BODY = {
    "address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400,
    "latitude": DEFAULT_SUBJECT_LATITUDE, "longitude": DEFAULT_SUBJECT_LONGITUDE,
}
client = TestClient(app)


class FakeSource(ComparableDataSource):
    """Comparables that all match the subject, one per rent. No network, no provider."""

    def __init__(self, rents):
        self.rents = rents

    def get_comparables(self, subject=None):
        return [
            {
                "address": f"{i} Test St", "rent": r,
                "latitude": DEFAULT_SUBJECT_LATITUDE, "longitude": DEFAULT_SUBJECT_LONGITUDE,
                "beds": 3, "baths": 2, "sqft": 1400,
            }
            for i, r in enumerate(self.rents)
        ]


def post_with(rents):
    app.dependency_overrides[get_comparable_source] = lambda: FakeSource(rents)
    try:
        return client.post("/valuation", json=BODY)
    finally:
        app.dependency_overrides.pop(get_comparable_source, None)


# --- the default ---------------------------------------------------------------------

def test_the_default_minimum_is_three():
    assert config.DEFAULT_MIN_COMPARABLES_REQUIRED == 3
    assert config.MIN_COMPARABLES_REQUIRED == 3


# --- engine: 0, 1, 2 and 3 comparables -----------------------------------------------

@pytest.mark.parametrize("rents", [[], [2000], [2000, 2200]])
def test_engine_refuses_fewer_than_three(rents):
    with pytest.raises(InsufficientDataError) as info:
        run_valuation(SUBJECT, FakeSource(rents))
    assert info.value.comparable_count == len(rents)
    assert info.value.minimum_required == 3


def test_engine_values_exactly_three():
    result = run_valuation(SUBJECT, FakeSource([2000, 2200, 2400]))
    assert result["comparable_count"] == 3
    assert result["median"] == 2200


def test_engine_values_more_than_three():
    assert run_valuation(SUBJECT, FakeSource([2000, 2200, 2400, 2600]))["comparable_count"] == 4


def test_insufficient_data_is_a_no_comparables_error():
    """Existing handlers for "nothing matched" keep working."""
    with pytest.raises(NoComparablesError):
        run_valuation(SUBJECT, FakeSource([2000]))


# --- the threshold counts what is used for pricing -----------------------------------

def test_threshold_applies_after_filtering():
    class Mixed(FakeSource):
        def get_comparables(self, subject=None):
            near = super().get_comparables(subject)
            far = [{**c, "latitude": 40.7, "longitude": -74.0} for c in near]  # beyond 1 mile
            return near + far

    with pytest.raises(InsufficientDataError) as info:
        run_valuation(SUBJECT, Mixed([2000, 2200]))  # 4 candidates, only 2 survive the filters
    assert info.value.comparable_count == 2


def test_threshold_applies_after_outlier_removal():
    # Five candidates pass the filters; the 20000 outlier is dropped, leaving four.
    rents = [2000, 2100, 2200, 2300, 20000]
    assert run_valuation(SUBJECT, FakeSource(rents), min_comparables=4)["comparable_count"] == 4
    with pytest.raises(InsufficientDataError) as info:
        run_valuation(SUBJECT, FakeSource(rents), min_comparables=5)
    assert (info.value.comparable_count, info.value.minimum_required) == (4, 5)


def test_the_minimum_can_be_overridden_per_call():
    assert run_valuation(SUBJECT, FakeSource([2000]), min_comparables=1)["recommended_rent"] == 2000
    with pytest.raises(InsufficientDataError):
        run_valuation(SUBJECT, FakeSource([2000, 2200, 2400]), min_comparables=4)


# --- messages and body ---------------------------------------------------------------

@pytest.mark.parametrize(
    "count, fragment",
    [(0, "No comparable properties found"), (1, "Only 1 comparable property "), (2, "Only 2 comparable properties ")],
)
def test_messages(count, fragment):
    error = InsufficientDataError(count, 3)
    assert fragment in str(error)
    assert "comparable" in str(error)


def test_to_response_shape():
    assert InsufficientDataError(2, 3).to_response() == {
        "status": "insufficient_data",
        "detail": "Only 2 comparable properties remained after filtering; at least 3 are required for a valuation.",
        "comparable_count": 2,
        "minimum_required": 3,
    }


# --- API: 0, 1, 2 and 3 comparables --------------------------------------------------

@pytest.mark.parametrize("rents", [[], [2000], [2000, 2200]])
def test_api_returns_insufficient_data_below_the_minimum(rents):
    response = post_with(rents)
    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["comparable_count"] == len(rents)
    assert body["minimum_required"] == 3
    assert "comparable" in body["detail"]
    assert "recommended_rent" not in body and "median" not in body  # no valuation


def test_api_returns_a_valuation_at_the_minimum():
    response = post_with([2000, 2200, 2400])
    assert response.status_code == 200
    assert response.json() == {
        "comparable_count": 3, "recommended_rent": 2200, "p25": 2100, "median": 2200,
        "p75": 2300, "average": 2200, "confidence": "low",
        "funnel": {
            "comparables_fetched": 3, "comparables_after_distance_filter": 3,
            "comparables_after_attribute_filter": 3, "comparables_after_outlier_filter": 3,
            "comparables_used": 3, "comparables_after_lookback_filter": 3,
        },
        "confidence_notes": [],
        "search": {
            "radius_miles": 1.0, "distance_units": "miles", "lookback_days": 90, "property_type": "all",
            "sqft_used": True, "minimum_comparables": 3, "nearest_listing_miles": 0.0,
        },
    }


def test_api_valuation_has_no_status_field():
    assert "status" not in post_with([2000, 2200, 2400]).json()


def test_insufficient_valuations_are_not_saved(repository):
    post_with([2000, 2200])
    assert repository.count_valuations() == 0
    post_with([2000, 2200, 2400])
    assert repository.count_valuations() == 1


def test_unknown_address_is_still_a_plain_404():
    response = client.post("/valuation", json={"address": "nowhere", "beds": 1, "baths": 1, "sqft": 500})
    assert response.status_code == 404
    assert "status" not in response.json()


# --- configuration -------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [(None, 3), ("", 3), ("  ", 3), ("1", 1), (" 5 ", 5)])
def test_env_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("MIN_COMPARABLES_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("MIN_COMPARABLES_REQUIRED", raw)
    assert config._min_comparables_from_env() == expected


@pytest.mark.parametrize("raw", ["0", "-2", "three", "2.5"])
def test_invalid_env_setting_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("MIN_COMPARABLES_REQUIRED", raw)
    with pytest.raises(ValueError, match="MIN_COMPARABLES_REQUIRED"):
        config._min_comparables_from_env()
