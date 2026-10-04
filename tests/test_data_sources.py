import pytest
from fastapi.testclient import TestClient

import app.services.valuation_engine as engine_module
from app.main import app
from app.routers.valuation import get_comparable_source
from app.schemas import ValuationRequest
from app.services.data_sources import ComparableDataSource, MockComparableSource
from app.services.valuation_engine import NoComparablesError, run_valuation

SUBJECT = ValuationRequest(address="123 Main St", beds=3, baths=2, sqft=1400)


class FakeSource(ComparableDataSource):
    """Four identical-ish matches, so the expected result is easy to compute by hand."""

    def __init__(self, rents):
        self.rents = rents

    def get_comparables(self):
        return [
            {"address": f"{i} Test St", "rent": r, "distance_miles": 0.5, "beds": 3, "baths": 2, "sqft": 1400}
            for i, r in enumerate(self.rents)
        ]


def test_interface_cannot_be_instantiated():
    with pytest.raises(TypeError):
        ComparableDataSource()  # type: ignore[abstract]


def test_mock_source_is_a_comparable_data_source():
    assert isinstance(MockComparableSource(), ComparableDataSource)


def test_mock_source_returns_the_26_comparables():
    comps = MockComparableSource().get_comparables()
    assert len(comps) == 26
    assert set(comps[0]) == {"address", "rent", "distance_miles", "beds", "baths", "sqft"}


def test_engine_uses_whatever_source_it_is_given():
    result = run_valuation(SUBJECT, FakeSource([2000, 2200, 2400, 2600]))
    assert result["comparable_count"] == 4
    assert result["median"] == 2300  # not the mock data's median
    assert result["recommended_rent"] == result["median"]


def test_engine_raises_when_the_source_has_no_matches():
    with pytest.raises(NoComparablesError):
        run_valuation(SUBJECT, FakeSource([]))


def test_engine_does_not_know_about_concrete_sources():
    assert not hasattr(engine_module, "MockComparableSource")


def test_api_uses_the_injected_source():
    app.dependency_overrides[get_comparable_source] = lambda: FakeSource([2000, 2200, 2400, 2600])
    try:
        response = TestClient(app).post(
            "/valuation", json={"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["median"] == 2300
