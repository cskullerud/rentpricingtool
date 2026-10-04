import socket

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE
from app.main import app
from app.routers.valuation import get_geocoder
from app.schemas import ValuationRequest
from app.services.data_sources import MockComparableSource
from app.services.geocoding import AddressNotFoundError, Geocoder, MockGeocoder
from app.services.geocoding.mock_geocoder import MOCK_ADDRESSES
from app.services.valuation_engine import run_valuation

client = TestClient(app)
SOURCE = MockComparableSource()
SUBJECT = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}
SAMPLE_RESPONSE = {
    "comparable_count": 16, "recommended_rent": 2512, "p25": 2419,
    "median": 2512, "p75": 2606, "average": 2505, "confidence": "high",
    "funnel": {
        "comparables_fetched": 26, "comparables_after_distance_filter": 24,
        "comparables_after_attribute_filter": 18, "comparables_after_outlier_filter": 16,
        "comparables_used": 16,
    },
}


class SpyGeocoder(Geocoder):
    """Records calls; returns fixed coordinates."""

    def __init__(self, latitude=DEFAULT_SUBJECT_LATITUDE, longitude=DEFAULT_SUBJECT_LONGITUDE):
        self.calls = []
        self._result = {"latitude": latitude, "longitude": longitude}

    def geocode(self, address):
        self.calls.append(address)
        return dict(self._result)


class ForbiddenGeocoder(Geocoder):
    def geocode(self, address):
        raise AssertionError("the geocoder must not be called when coordinates are supplied")


def request(**overrides):
    return ValuationRequest(**{**SUBJECT, **overrides})


# --- the interface -------------------------------------------------------------------

def test_interface_cannot_be_instantiated():
    with pytest.raises(TypeError):
        Geocoder()  # type: ignore[abstract]


def test_mock_geocoder_is_a_geocoder():
    assert isinstance(MockGeocoder(), Geocoder)


# --- known addresses -----------------------------------------------------------------

def test_known_address_returns_coordinates():
    assert MockGeocoder().geocode("123 Main St, La Mesa, CA") == {
        "latitude": 32.7678, "longitude": -117.0231,
    }


def test_the_three_spec_examples_resolve():
    geocoder = MockGeocoder()
    for address in ("123 Main St, La Mesa, CA", "456 Palm Ave, La Mesa, CA", "789 Broadway, San Diego, CA"):
        result = geocoder.geocode(address)
        assert set(result) == {"latitude", "longitude"}


def test_at_least_ten_addresses_are_supported_and_all_are_near_san_diego():
    assert len(MOCK_ADDRESSES) >= 10
    geocoder = MockGeocoder()
    for address in MOCK_ADDRESSES:
        result = geocoder.geocode(address)
        assert 32.6 <= result["latitude"] <= 32.9, address
        assert -117.3 <= result["longitude"] <= -116.9, address


def test_main_street_address_sits_on_the_default_subject_point():
    assert MockGeocoder().geocode("123 Main St, La Mesa, CA") == {
        "latitude": DEFAULT_SUBJECT_LATITUDE, "longitude": DEFAULT_SUBJECT_LONGITUDE,
    }


@pytest.mark.parametrize("variant", [
    "123 main st, la mesa, ca",
    "  123   Main   St ,  La Mesa , CA  ",
    "123 Main St., La Mesa, CA",
    "123 Main St, La Mesa, California",
])
def test_matching_ignores_case_spacing_periods_and_california(variant):
    assert MockGeocoder().geocode(variant) == MockGeocoder().geocode("123 Main St, La Mesa, CA")


def test_a_shorter_address_matches_when_it_is_unambiguous():
    geocoder = MockGeocoder()
    expected = geocoder.geocode("456 Palm Ave, La Mesa, CA")
    assert geocoder.geocode("456 Palm Ave") == expected
    assert geocoder.geocode("456 Palm Ave, La Mesa") == expected


def test_results_are_copies():
    geocoder = MockGeocoder()
    geocoder.geocode("123 Main St, La Mesa, CA")["latitude"] = 0
    assert geocoder.geocode("123 Main St, La Mesa, CA")["latitude"] == 32.7678


def test_geocoding_makes_no_network_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", forbidden)
    MockGeocoder().geocode("123 Main St, La Mesa, CA")


# --- unknown, ambiguous and blank addresses ------------------------------------------

def test_unknown_address_raises():
    with pytest.raises(AddressNotFoundError) as excinfo:
        MockGeocoder().geocode("1 Nowhere Rd, Atlantis, CA")
    assert excinfo.value.address == "1 Nowhere Rd, Atlantis, CA"
    assert "1 Nowhere Rd, Atlantis, CA" in str(excinfo.value)
    assert "latitude and longitude" in str(excinfo.value)  # tells the caller what to do


def test_a_partial_match_that_is_not_a_prefix_is_not_found():
    with pytest.raises(AddressNotFoundError):
        MockGeocoder().geocode("Main St")


def test_ambiguous_street_raises_and_asks_for_the_city():
    geocoder = MockGeocoder()
    with pytest.raises(AddressNotFoundError) as excinfo:
        geocoder.geocode("100 University Ave")
    assert "ambiguous" in str(excinfo.value)
    assert geocoder.geocode("100 University Ave, La Mesa, CA") != geocoder.geocode(
        "100 University Ave, San Diego, CA"
    )


@pytest.mark.parametrize("blank", ["", "   ", ","])
def test_blank_address_raises(blank):
    with pytest.raises(AddressNotFoundError) as excinfo:
        MockGeocoder().geocode(blank)
    assert "empty" in str(excinfo.value)


# --- in the valuation flow (engine) --------------------------------------------------

def test_supplied_coordinates_bypass_the_geocoder():
    subject = request(latitude=DEFAULT_SUBJECT_LATITUDE, longitude=DEFAULT_SUBJECT_LONGITUDE)
    result = run_valuation(subject, SOURCE, geocoder=ForbiddenGeocoder())
    assert result["comparable_count"] == 16


def test_supplied_coordinates_win_over_what_the_address_would_geocode_to():
    spy = SpyGeocoder()
    subject = request(latitude=40.7128, longitude=-74.0060)  # New York: nothing nearby
    with pytest.raises(Exception, match="No comparable"):
        run_valuation(subject, SOURCE, geocoder=spy)
    assert spy.calls == []


def test_without_coordinates_the_address_is_geocoded_once_and_its_result_used():
    spy = SpyGeocoder()
    result = run_valuation(request(address="anything at all"), SOURCE, geocoder=spy)
    assert spy.calls == ["anything at all"]
    assert result["comparable_count"] == 16


def test_the_geocoded_location_is_what_the_distance_filter_uses():
    far = SpyGeocoder(latitude=40.7128, longitude=-74.0060)
    with pytest.raises(Exception, match="No comparable"):
        run_valuation(request(), SOURCE, geocoder=far)


def test_an_unknown_address_raises_from_the_engine_and_nothing_is_saved(repository):
    with pytest.raises(AddressNotFoundError):
        run_valuation(request(address="1 Nowhere Rd"), SOURCE, repository, MockGeocoder())
    assert repository.count_valuations() == 0


def test_without_a_geocoder_the_engine_keeps_using_the_default_point():
    assert run_valuation(request(address="unknown to everyone"), SOURCE)["comparable_count"] == 16


# --- through the API -----------------------------------------------------------------

def test_valuation_with_address_only():
    response = client.post("/valuation", json=SUBJECT)
    assert response.status_code == 200
    assert response.json() == SAMPLE_RESPONSE


def test_valuation_with_the_full_address_only():
    response = client.post("/valuation", json={**SUBJECT, "address": "123 Main St, La Mesa, CA"})
    assert response.status_code == 200
    assert response.json() == SAMPLE_RESPONSE


def test_valuation_with_coordinates_only_does_not_need_a_known_address():
    response = client.post(
        "/valuation",
        json={**SUBJECT, "address": "not a real address", "latitude": 32.7678, "longitude": -117.0231},
    )
    assert response.status_code == 200
    assert response.json() == SAMPLE_RESPONSE


def test_unknown_address_returns_404_with_a_useful_message():
    response = client.post("/valuation", json={**SUBJECT, "address": "1 Nowhere Rd, Atlantis, CA"})
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "1 Nowhere Rd, Atlantis, CA" in detail
    assert "latitude and longitude" in detail


def test_ambiguous_address_returns_404_asking_for_the_city():
    response = client.post("/valuation", json={**SUBJECT, "address": "100 University Ave"})
    assert response.status_code == 404
    assert "ambiguous" in response.json()["detail"]


def test_blank_address_without_coordinates_returns_404():
    response = client.post("/valuation", json={**SUBJECT, "address": "  "})
    assert response.status_code == 404


def test_blank_address_is_fine_when_coordinates_are_given():
    response = client.post(
        "/valuation", json={**SUBJECT, "address": "", "latitude": 32.7678, "longitude": -117.0231}
    )
    assert response.status_code == 200


def test_no_comparables_and_unknown_address_have_different_messages():
    unknown = client.post("/valuation", json={**SUBJECT, "address": "1 Nowhere Rd"}).json()["detail"]
    nothing_nearby = client.post(
        "/valuation", json={**SUBJECT, "latitude": 40.7128, "longitude": -74.0060}
    ).json()["detail"]
    assert unknown != nothing_nearby
    assert "comparable" in nothing_nearby


@pytest.mark.parametrize("address", sorted(a for a in MOCK_ADDRESSES if "100 University" not in a))
def test_geocoding_an_address_equals_supplying_its_coordinates(address):
    found = MockGeocoder().geocode(address)
    by_address = client.post("/valuation", json={**SUBJECT, "address": address})
    by_coordinates = client.post("/valuation", json={**SUBJECT, **found})
    assert by_address.status_code == by_coordinates.status_code
    if by_address.status_code == 200:
        assert by_address.json() == by_coordinates.json()


def test_the_api_uses_the_injected_geocoder():
    spy = SpyGeocoder(latitude=40.7128, longitude=-74.0060)
    app.dependency_overrides[get_geocoder] = lambda: spy
    try:
        response = client.post("/valuation", json=SUBJECT)
    finally:
        app.dependency_overrides.pop(get_geocoder, None)
    assert spy.calls == ["123 Main St"]
    assert response.status_code == 404  # the fake put the subject in New York


def test_the_api_does_not_call_the_geocoder_when_coordinates_are_supplied():
    app.dependency_overrides[get_geocoder] = lambda: ForbiddenGeocoder()
    try:
        response = client.post(
            "/valuation", json={**SUBJECT, "latitude": 32.7678, "longitude": -117.0231}
        )
    finally:
        app.dependency_overrides.pop(get_geocoder, None)
    assert response.status_code == 200


def test_a_lone_coordinate_is_still_rejected_rather_than_geocoded():
    assert client.post("/valuation", json={**SUBJECT, "latitude": 32.77}).status_code == 422
