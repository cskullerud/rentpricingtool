"""Address-only valuations through the real geocoder wiring (GEOCODER=census) with a fake Census
transport and mock comparables. Nothing here can reach the network or RentCast."""
import json
import logging
import re
import urllib.parse
import urllib.request
from html import unescape

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.valuation import get_comparable_source
from app.services.data_sources import ComparableDataSource, RentCastComparableSource
from app.services.data_sources import rentcast_source
from app.services.geocoding import census_geocoder

LAT, LON = 32.7678, -117.0231
ADDRESS = "123 Main St, San Diego, CA 92101"
JSON_BODY = {"address": ADDRESS, "beds": 3, "baths": 2, "sqft": 1400}
FORM = {"address": ADDRESS, "beds": "3", "baths": "2", "sqft": "1400", "latitude": "", "longitude": ""}


def census_body(*points):
    matches = [{"coordinates": {"x": lon, "y": lat}, "matchedAddress": "X"} for lat, lon in points]
    return json.dumps({"result": {"addressMatches": matches}}).encode()


class CensusFake:
    """A fake Census transport that records every request."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, census_body((LAT, LON)))]
        self.urls = []

    def __call__(self, url, headers, timeout):
        self.urls.append(url)
        response = self.responses[min(len(self.urls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def addresses(self):
        return [urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["address"][0] for u in self.urls]


class ListSource(ComparableDataSource):
    def __init__(self, n=4):
        self.rents = [2000 + 100 * i for i in range(n)]
        self.calls = 0

    def get_comparables(self, subject=None):
        self.calls += 1
        return [
            {"address": f"{i} Test St", "rent": r, "latitude": LAT, "longitude": LON, "beds": 3, "baths": 2, "sqft": 1400}
            for i, r in enumerate(self.rents)
        ]


@pytest.fixture(autouse=True)
def census_environment(monkeypatch):
    for name in ("DATA_PROVIDER", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEOCODER", "census")
    monkeypatch.setenv("GEOCODER_MAX_RETRIES", "0")  # no real sleeping in these tests

    def blocked(*args, **kwargs):
        raise AssertionError("a test tried to use the network")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)


@pytest.fixture
def census(monkeypatch):
    fake = CensusFake()
    monkeypatch.setattr(census_geocoder, "urllib_transport", fake)
    return fake


@pytest.fixture
def source():
    comparables = ListSource()
    app.dependency_overrides[get_comparable_source] = lambda: comparables
    yield comparables
    app.dependency_overrides.pop(get_comparable_source, None)


@pytest.fixture
def client():
    return TestClient(app)


def token_of(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def submit(client, **fields):
    page = client.get("/ui/")
    return client.post("/ui/valuation", data={**FORM, "csrf_token": token_of(page), **fields})


def text_of(html, element_id):
    match = re.search(rf'<(\w+)[^>]*id="{element_id}"[^>]*>(.*?)</\1>', html, re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", match.group(2)))).strip() if match else None


# --- an address-only valuation works ------------------------------------------------------------------------

def test_api_address_only_valuation(client, census, source):
    response = client.post("/valuation", json=JSON_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["comparable_count"] == 4 and body["median"] == 2150 and body["confidence"] == "low"
    assert census.addresses == [ADDRESS] and source.calls == 1


def test_ui_address_only_valuation(client, census, source):
    response = submit(client)
    assert response.status_code == 200 and 'id="result-card"' in response.text
    assert text_of(response.text, "recommended-rent").startswith("$2,150")
    assert census.addresses == [ADDRESS]


def test_the_ui_and_the_api_agree(client, census, source):
    api = client.post("/valuation", json=JSON_BODY).json()
    ui = submit(client).text
    assert f"${api['median']:,}" in ui and f"Based on {api['comparable_count']} comparable" in ui


def test_the_address_as_typed_is_what_gets_saved(client, census, source, repository):
    submit(client, address="123 Main St Apt 4B, San Diego, CA 92101")
    assert repository.get_recent_valuations(1)[0]["address"] == "123 Main St Apt 4B, San Diego, CA 92101"
    assert census.addresses == ["123 Main St, San Diego, CA 92101"]  # but the lookup used the cleaned one


# --- caching across requests -----------------------------------------------------------------------------------

def test_the_second_valuation_for_an_address_makes_no_lookup(client, census, source):
    client.post("/valuation", json=JSON_BODY)
    client.post("/valuation", json={**JSON_BODY, "beds": 2})
    submit(client)
    assert len(census.urls) == 1


@pytest.mark.parametrize("variant", ["123 MAIN ST, SAN DIEGO, CA 92101", "123 Main St Apt 9, San Diego, California 92101", "123 Main St #2, San Diego, CA 92101"])
def test_other_spellings_and_units_use_the_cached_answer(client, census, source, variant):
    client.post("/valuation", json=JSON_BODY)
    assert client.post("/valuation", json={**JSON_BODY, "address": variant}).status_code == 200
    assert len(census.urls) == 1


def test_a_different_address_makes_a_new_lookup(client, census, source):
    client.post("/valuation", json=JSON_BODY)
    client.post("/valuation", json={**JSON_BODY, "address": "9 Other St, San Diego, CA 92101"})
    assert len(census.urls) == 2


# --- coordinates skip the geocoder ---------------------------------------------------------------------------------

def test_coordinates_in_the_api_request_skip_the_lookup(client, census, source):
    assert client.post("/valuation", json={**JSON_BODY, "latitude": LAT, "longitude": LON}).status_code == 200
    assert census.urls == []


def test_coordinates_in_the_form_skip_the_lookup(client, census, source):
    response = submit(client, latitude=str(LAT), longitude=str(LON))
    assert response.status_code == 200 and 'id="result-card"' in response.text
    assert census.urls == []


# --- address not found --------------------------------------------------------------------------------------------------

@pytest.fixture
def no_match(monkeypatch):
    fake = CensusFake((200, census_body()))
    monkeypatch.setattr(census_geocoder, "urllib_transport", fake)
    return fake


def test_api_reports_an_unknown_address(client, no_match, source):
    response = client.post("/valuation", json=JSON_BODY)
    assert response.status_code == 404 and "Could not geocode address" in response.json()["detail"]
    assert source.calls == 0


def test_ui_reports_an_unknown_address_on_the_field(client, no_match, source, repository):
    response = submit(client)
    assert response.status_code == 422
    assert "Address not found" in text_of(response.text, "address-error")
    assert 'class="collapse show mt-2" id="coordinates"' in response.text
    assert f'value="{ADDRESS}"' in response.text
    assert source.calls == 0 and repository.count_valuations() == 0


def test_unknown_addresses_are_remembered_briefly(client, no_match, source):
    client.post("/valuation", json=JSON_BODY)
    client.post("/valuation", json=JSON_BODY)
    assert len(no_match.urls) == 1


def test_ambiguous_addresses_ask_for_more_detail(client, monkeypatch, source):
    fake = CensusFake((200, census_body((38.846, -76.927), (34.05, -118.24))))
    monkeypatch.setattr(census_geocoder, "urllib_transport", fake)
    api = client.post("/valuation", json={**JSON_BODY, "address": "100 Main St"})
    assert api.status_code == 404 and "ambiguous" in api.json()["detail"]
    ui = submit(TestClient(app), address="100 Main St")
    assert ui.status_code == 422 and "ambiguous" in text_of(ui.text, "address-error")


# --- the service is down or answers badly ------------------------------------------------------------------------------

@pytest.fixture
def down(monkeypatch):
    fake = CensusFake((503, b""))
    monkeypatch.setattr(census_geocoder, "urllib_transport", fake)
    return fake


def test_api_reports_an_outage_without_details(client, down, source):
    response = client.post("/valuation", json=JSON_BODY)
    assert response.status_code == 503
    assert response.json() == {"detail": "The address lookup service is unavailable; try again shortly, or enter exact coordinates"}
    assert source.calls == 0


def test_ui_reports_an_outage_and_keeps_the_entries(client, down, source, repository):
    response = submit(client, address="77 Secret Ln, Hidden, CA 90000")
    assert response.status_code == 503
    assert "address lookup service is unavailable" in text_of(response.text, "page-alert")
    assert 'value="77 Secret Ln, Hidden, CA 90000"' in response.text and 'value="1400"' in response.text
    assert "geocoding.geo" not in response.text and "503" not in text_of(response.text, "page-alert")
    assert source.calls == 0 and repository.count_valuations() == 0


def test_an_outage_is_not_cached_so_the_next_try_works(client, monkeypatch, source):
    fake = CensusFake((503, b""), (200, census_body((LAT, LON))))
    monkeypatch.setattr(census_geocoder, "urllib_transport", fake)
    assert client.post("/valuation", json=JSON_BODY).status_code == 503
    assert client.post("/valuation", json=JSON_BODY).status_code == 200
    assert len(fake.urls) == 2


def test_addresses_already_cached_still_work_during_an_outage(client, monkeypatch, source):
    fake = CensusFake((200, census_body((LAT, LON))), (503, b""))
    monkeypatch.setattr(census_geocoder, "urllib_transport", fake)
    assert client.post("/valuation", json=JSON_BODY).status_code == 200
    assert client.post("/valuation", json=JSON_BODY).status_code == 200  # served from the cache
    assert client.post("/valuation", json={**JSON_BODY, "address": "9 New St, San Diego, CA 92101"}).status_code == 503


def test_a_garbled_answer_is_a_502(client, monkeypatch, source):
    monkeypatch.setattr(census_geocoder, "urllib_transport", CensusFake((200, b"garbage")))
    api = client.post("/valuation", json=JSON_BODY)
    assert api.status_code == 502 and "unexpected response" in api.json()["detail"]
    ui = submit(TestClient(app), address="9 Other St, X, CA")
    assert ui.status_code == 502 and "unexpected response" in text_of(ui.text, "page-alert")


def test_network_errors_become_an_outage_message(client, monkeypatch, source):
    monkeypatch.setattr(census_geocoder, "urllib_transport", CensusFake(census_geocoder.GeocoderUnavailableError("no route to host")))
    response = client.post("/valuation", json=JSON_BODY)
    assert response.status_code == 503 and "no route" not in response.text


# --- RentCast is never reached when the address cannot be located -------------------------------------------------------------

def test_rentcast_is_not_called_when_the_address_is_not_found(client, no_match, monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key-not-real")
    calls = []
    original = RentCastComparableSource.get_comparables
    monkeypatch.setattr(RentCastComparableSource, "get_comparables", lambda self, subject=None: calls.append(subject) or original(self, subject))
    assert client.post("/valuation", json=JSON_BODY).status_code == 404
    assert submit(TestClient(app)).status_code == 422
    assert calls == []  # the real RentCast source was selected but never asked for listings


def test_rentcast_is_not_called_when_the_lookup_service_is_down(client, down, monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key-not-real")
    calls = []
    monkeypatch.setattr(RentCastComparableSource, "get_comparables", lambda self, subject=None: calls.append(subject))
    assert client.post("/valuation", json=JSON_BODY).status_code == 503
    assert calls == []


def test_the_rentcast_source_receives_the_geocoded_location(client, census, monkeypatch):
    """With RentCast selected, the subject it is asked about carries the geocoded coordinates."""
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key-not-real")
    seen = []
    monkeypatch.setattr(RentCastComparableSource, "get_comparables", lambda self, subject=None: seen.append(subject) or [])
    client.post("/valuation", json=JSON_BODY)  # no comparables -> insufficient data, which is fine here
    assert (seen[0].latitude, seen[0].longitude) == (LAT, LON) and seen[0].address == ADDRESS


# --- logging ---------------------------------------------------------------------------------------------------------------------

def test_lookups_are_logged_without_the_address(client, census, source, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/valuation", json={**JSON_BODY, "address": "77 Secret Ln, Hidden, CA 90000"})
        client.post("/valuation", json={**JSON_BODY, "address": "77 Secret Ln, Hidden, CA 90000"})
    geocode_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("geocode ")]
    assert any("source=census result=match" in m for m in geocode_lines)
    assert any("cache=hit" in m for m in geocode_lines)
    assert "Secret" not in caplog.text and "Hidden" not in caplog.text


def test_the_funnel_and_confidence_are_unchanged(client, census, source):
    body = client.post("/valuation", json=JSON_BODY).json()
    assert body["funnel"] == {
        "comparables_fetched": 4, "comparables_after_distance_filter": 4, "comparables_after_attribute_filter": 4,
        "comparables_after_outlier_filter": 4, "comparables_used": 4, "comparables_after_lookback_filter": 4,
    }


def test_mock_comparable_defaults_are_untouched_by_the_new_wiring(client, monkeypatch):
    """GEOCODER=mock still gives the original demo behaviour (and never calls Census)."""
    monkeypatch.setenv("GEOCODER", "mock")
    fake = CensusFake()
    monkeypatch.setattr(census_geocoder, "urllib_transport", fake)
    assert client.post("/valuation", json={"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}).json()["recommended_rent"] == 2512
    assert client.post("/valuation", json=JSON_BODY).status_code == 404  # not a demo address
    assert fake.urls == []
