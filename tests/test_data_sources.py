import pytest
from fastapi.testclient import TestClient

import app.services.valuation_engine as engine_module
from app.config import DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE
from app.main import app
from app.routers.valuation import get_comparable_source
from app.schemas import ValuationRequest
from app.services.data_sources import ComparableDataSource, MockComparableSource, SubjectProperty
from app.services.geocoding import Geocoder
from app.services.valuation_engine import NoComparablesError, run_valuation

SUBJECT = ValuationRequest(address="123 Main St", beds=3, baths=2, sqft=1400)


class FakeSource(ComparableDataSource):
    """Four identical-ish matches, so the expected result is easy to compute by hand."""

    def __init__(self, rents):
        self.rents = rents
        self.subjects = []

    def get_comparables(self, subject=None):
        self.subjects.append(subject)
        return [
            {
                "address": f"{i} Test St", "rent": r,
                "latitude": DEFAULT_SUBJECT_LATITUDE, "longitude": DEFAULT_SUBJECT_LONGITUDE,
                "beds": 3, "baths": 2, "sqft": 1400,
            }
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
    assert set(comps[0]) == {"address", "rent", "latitude", "longitude", "beds", "baths", "sqft"}


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
        app.dependency_overrides.pop(get_comparable_source, None)
    assert response.status_code == 200
    assert response.json()["median"] == 2300


def test_get_comparables_subject_is_optional():
    subject = SubjectProperty("1 A St", DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE, 3, 2, 1400)
    mock = MockComparableSource()
    assert mock.get_comparables() == mock.get_comparables(subject)
    assert mock.get_comparables() == mock.get_comparables(subject=None)


def test_engine_passes_the_subject_with_given_coordinates():
    source = FakeSource([2000, 2200, 2400, 2600])
    request = ValuationRequest(
        address="9 Elm St", beds=3, baths=2, sqft=1400,
        latitude=DEFAULT_SUBJECT_LATITUDE, longitude=DEFAULT_SUBJECT_LONGITUDE,
    )
    run_valuation(request, source)
    assert source.subjects == [
        SubjectProperty("9 Elm St", DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE, 3, 2.0, 1400)
    ]


def test_engine_passes_the_geocoded_location_to_the_source():
    class FixedGeocoder(Geocoder):
        def geocode(self, address):
            return {"latitude": DEFAULT_SUBJECT_LATITUDE, "longitude": DEFAULT_SUBJECT_LONGITUDE}

    source = FakeSource([2000, 2200, 2400, 2600])
    run_valuation(SUBJECT, source, geocoder=FixedGeocoder())
    assert source.subjects[0].latitude == DEFAULT_SUBJECT_LATITUDE
    assert source.subjects[0].address == "123 Main St"


def test_engine_passes_the_default_location_without_a_geocoder():
    source = FakeSource([2000, 2200, 2400, 2600])
    run_valuation(SUBJECT, source)
    assert source.subjects[0].longitude == DEFAULT_SUBJECT_LONGITUDE
