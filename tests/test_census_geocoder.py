import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import pytest

from app.config import VERSION
from app.services.data_sources import DataSourceError, ProviderConfigurationError
from app.services.geocoding import (
    AddressNotFoundError,
    CensusGeocoder,
    CensusSettings,
    Geocoder,
    GeocoderConfigurationError,
    GeocoderResponseError,
    GeocoderUnavailableError,
)
from app.services.geocoding import census_geocoder

ADDRESS = "4600 Silver Hill Rd, Washington, DC 20233"


def census_body(*points, **extra):
    """A response in the shape the Census one-line-address service documents: matches have
    coordinates x (longitude) and y (latitude). Replace with a recorded response after the
    approved live check (see test_census_live.py)."""
    matches = [
        {
            "tigerLine": {"side": "L", "tigerLineId": "76355984"},
            "coordinates": {"x": lon, "y": lat},
            "matchedAddress": ADDRESS.upper(),
            "addressComponents": {"zip": "20233", "city": "WASHINGTON", "state": "DC"},
        }
        for lat, lon in points
    ]
    return json.dumps({"result": {"input": {"benchmark": {"benchmarkName": "Public_AR_Current"}}, "addressMatches": matches, **extra}}).encode()


SILVER_HILL = census_body((38.84601622386617, -76.92748724230096))


class FakeTransport:
    """Answers each (status, body) in turn (or raises an Exception), then repeats the last."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, SILVER_HILL)]
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


def make(transport=None, sleeps=None, **settings):
    sleeps = [] if sleeps is None else sleeps
    return CensusGeocoder(CensusSettings(**settings), transport=transport or FakeTransport(), sleep=sleeps.append)


def query_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("a Census geocoder test tried to use the network")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)


# --- a successful lookup ---------------------------------------------------------------------------------

def test_is_a_geocoder():
    assert isinstance(make(), Geocoder)


def test_returns_latitude_and_longitude_in_the_right_order():
    result = make().geocode(ADDRESS)
    assert result == {"latitude": 38.846016, "longitude": -76.927487}  # x is longitude, y is latitude


def test_coordinates_are_rounded_to_six_decimals():
    result = make(FakeTransport((200, census_body((32.123456789, -117.987654321))))).geocode("1 A St, X, CA")
    assert result == {"latitude": 32.123457, "longitude": -117.987654}


def test_a_swap_of_x_and_y_would_be_caught_by_the_range_check():
    # y (latitude) of -117 is impossible, so a response with x and y reversed is refused, not used
    swapped = census_body((-117.02, 32.76))
    with pytest.raises(GeocoderResponseError, match="invalid coordinates"):
        make(FakeTransport((200, swapped))).geocode("1 A St, X, CA")


def test_request_url_headers_and_timeout():
    transport = FakeTransport()
    make(transport, timeout_seconds=7).geocode(ADDRESS)
    url, headers, timeout = transport.calls[0]
    parsed = urllib.parse.urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    assert query_of(url) == {"address": [ADDRESS], "benchmark": ["Public_AR_Current"], "format": ["json"]}
    assert headers["User-Agent"] == f"rentpricingtool/{VERSION}" and headers["Accept"] == "application/json"
    assert timeout == 7


def test_one_request_per_lookup():
    transport = FakeTransport()
    make(transport).geocode(ADDRESS)
    assert len(transport.calls) == 1


# --- what is sent -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "typed, sent",
    [
        ("123 Main St Apt 4B, San Diego, CA 92101", "123 Main St, San Diego, CA 92101"),
        ("123 Main St #7, San Diego, CA 92101", "123 Main St, San Diego, CA 92101"),
        ("123 Main St, Unit B, San Diego, CA 92101", "123 Main St, San Diego, CA 92101"),
        ("123 Main St Suite 100, San Diego, CA 92101", "123 Main St, San Diego, CA 92101"),
        ("  123   Main St ,San Diego,  CA 92101 ", "123 Main St, San Diego, CA 92101"),
    ],
)
def test_units_and_spacing_are_cleaned_before_lookup(typed, sent):
    transport = FakeTransport()
    make(transport).geocode(typed)
    assert query_of(transport.calls[0][0])["address"] == [sent]


@pytest.mark.parametrize(
    "tricky",
    [
        "1 Main St&benchmark=evil, X, CA",
        "1 Main St#frag, X, CA",
        "1 Main St?format=xml, X, CA",
        "1 Main St=2, X, CA",
        "1 Mäin Straße, Köln, CA",
        "1 Main St, X, CA; DROP TABLE",
        "1 Main St%26benchmark%3Devil, X, CA",
        "1 Main St+Apt, X, CA",
    ],
)
def test_the_address_cannot_change_the_rest_of_the_query(tricky):
    transport = FakeTransport()
    make(transport).geocode(tricky)
    query = query_of(transport.calls[0][0])
    assert set(query) == {"address", "benchmark", "format"}
    assert query["benchmark"] == ["Public_AR_Current"] and query["format"] == ["json"]
    assert len(query["address"]) == 1


def test_the_original_address_is_kept_in_the_not_found_message():
    with pytest.raises(AddressNotFoundError) as info:
        make(FakeTransport((200, census_body()))).geocode("999 Nowhere Blvd Apt 3, Atlantis, CA")
    assert info.value.address == "999 Nowhere Blvd Apt 3, Atlantis, CA"


# --- input checks happen before any request --------------------------------------------------------------

@pytest.mark.parametrize("address", ["", "   ", None, "Apt 4B", "#12", ",,,"])
def test_empty_addresses_are_refused_without_a_request(address):
    transport = FakeTransport()
    with pytest.raises(AddressNotFoundError, match="address is empty"):
        make(transport).geocode(address)
    assert transport.calls == []


def test_overlong_addresses_are_refused_without_a_request():
    transport = FakeTransport()
    with pytest.raises(AddressNotFoundError, match="too long"):
        make(transport).geocode("1 Main St, " + "x" * 200)
    assert transport.calls == []


def test_the_limit_is_on_the_cleaned_address():
    transport = FakeTransport()
    make(transport).geocode("1 Main St" + " " * 300 + ", X, CA")
    assert len(transport.calls) == 1


# --- no match, several matches ----------------------------------------------------------------------------

def test_no_match_is_address_not_found():
    with pytest.raises(AddressNotFoundError) as info:
        make(FakeTransport((200, census_body()))).geocode("999 Nowhere Blvd, Atlantis, CA")
    assert info.value.reason == "address not found; check it and include the city and state"


def test_no_match_is_not_retried():
    transport = FakeTransport((200, census_body()))
    with pytest.raises(AddressNotFoundError):
        make(transport).geocode("999 Nowhere Blvd, Atlantis, CA")
    assert len(transport.calls) == 1


def test_several_matches_at_one_place_use_the_first():
    body = census_body((38.8460, -76.9274), (38.8461, -76.9275), (38.8462, -76.9276))
    assert make(FakeTransport((200, body))).geocode(ADDRESS) == {"latitude": 38.846, "longitude": -76.9274}


def test_matches_far_apart_are_ambiguous():
    body = census_body((38.846, -76.927), (34.05, -118.24))  # Washington DC and Los Angeles
    with pytest.raises(AddressNotFoundError, match="ambiguous; include the city, state and ZIP code"):
        make(FakeTransport((200, body))).geocode("100 Main St")


def test_matches_just_over_the_ambiguity_distance_are_ambiguous():
    # 0.01 degrees of latitude is about 0.69 miles
    body = census_body((38.00, -77.00), (38.01, -77.00))
    with pytest.raises(AddressNotFoundError, match="ambiguous"):
        make(FakeTransport((200, body))).geocode("100 Main St")


def test_matches_just_under_the_ambiguity_distance_are_accepted():
    body = census_body((38.000, -77.00), (38.006, -77.00))  # about 0.41 miles
    assert make(FakeTransport((200, body))).geocode("100 Main St")["latitude"] == 38.0


# --- unusable responses -----------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        b"not json at all", b"", b"[]", b'"text"', b"null", b"{}", b'{"result": null}', b'{"result": []}',
        b'{"result": {}}', b'{"result": {"addressMatches": null}}', b'{"result": {"addressMatches": {}}}',
        b'{"result": {"addressMatches": ["x"]}}', b'{"result": {"addressMatches": [{}]}}',
        b'{"result": {"addressMatches": [{"coordinates": null}]}}',
        b'{"result": {"addressMatches": [{"coordinates": {}}]}}',
        b'{"result": {"addressMatches": [{"coordinates": {"x": "-76.9", "y": "38.8"}}]}}',
        b'{"result": {"addressMatches": [{"coordinates": {"x": true, "y": false}}]}}',
        b'{"result": {"addressMatches": [{"coordinates": {"x": null, "y": 38.8}}]}}',
        b'{"result": {"addressMatches": [{"coordinates": {"x": NaN, "y": 38.8}}]}}',
        b'{"result": {"addressMatches": [{"coordinates": {"x": Infinity, "y": 38.8}}]}}',
        b'{"result": {"addressMatches": [{"coordinates": {"x": -76.9, "y": 91}}]}}',
        b'{"result": {"addressMatches": [{"coordinates": {"x": -181, "y": 38.8}}]}}',
    ],
)
def test_unexpected_bodies_raise_a_response_error(body):
    with pytest.raises(GeocoderResponseError):
        make(FakeTransport((200, body))).geocode(ADDRESS)


def test_one_bad_match_among_good_ones_is_not_silently_skipped():
    body = json.dumps({"result": {"addressMatches": [
        {"coordinates": {"x": -76.9, "y": 38.8}}, {"coordinates": {"x": "bad", "y": 38.8}}]}}).encode()
    with pytest.raises(GeocoderResponseError):
        make(FakeTransport((200, body))).geocode(ADDRESS)


@pytest.mark.parametrize("body", [b"<html><body>Service busy</body></html>", b"  \n<!DOCTYPE html><html>maintenance</html>"])
def test_a_web_page_instead_of_data_means_the_service_is_busy(body):
    with pytest.raises(GeocoderUnavailableError, match="web page"):
        make(FakeTransport((200, body)), max_retries=0).geocode(ADDRESS)


# --- HTTP statuses --------------------------------------------------------------------------------------

@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_busy_or_down_statuses_are_unavailable_after_the_retry(status):
    transport = FakeTransport((status, b""))
    with pytest.raises(GeocoderUnavailableError):
        make(transport).geocode(ADDRESS)
    assert len(transport.calls) == 2  # one try and the default single retry


@pytest.mark.parametrize("status", [400, 401, 403, 404, 301, 302])
def test_other_statuses_are_response_errors_and_are_not_retried(status):
    transport = FakeTransport((status, b'{"errors": ["x"]}'))
    with pytest.raises(GeocoderResponseError):
        make(transport).geocode(ADDRESS)
    assert len(transport.calls) == 1


def test_a_response_error_is_not_an_address_not_found():
    with pytest.raises(GeocoderResponseError) as info:
        make(FakeTransport((400, b"{}"))).geocode(ADDRESS)
    assert not isinstance(info.value, AddressNotFoundError)


# --- retries --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("first", [(503, b""), (429, b""), GeocoderUnavailableError("timed out"), (200, b"<html>busy</html>")])
def test_a_transient_failure_is_retried_once_and_can_succeed(first):
    transport, sleeps = FakeTransport(first, (200, SILVER_HILL)), []
    result = make(transport, sleeps=sleeps).geocode(ADDRESS)
    assert result["latitude"] == 38.846016
    assert len(transport.calls) == 2 and sleeps == [0.5]


def test_retries_are_bounded_and_back_off():
    transport, sleeps = FakeTransport((503, b"")), []
    with pytest.raises(GeocoderUnavailableError):
        make(transport, sleeps=sleeps, max_retries=3, retry_delay_seconds=1).geocode(ADDRESS)
    assert len(transport.calls) == 4 and sleeps == [1, 2, 4]


def test_no_retries_when_set_to_zero():
    transport, sleeps = FakeTransport((503, b"")), []
    with pytest.raises(GeocoderUnavailableError):
        make(transport, sleeps=sleeps, max_retries=0).geocode(ADDRESS)
    assert len(transport.calls) == 1 and sleeps == []


def test_a_response_error_is_never_retried():
    transport, sleeps = FakeTransport((200, b"garbage")), []
    with pytest.raises(GeocoderResponseError):
        make(transport, sleeps=sleeps, max_retries=3).geocode(ADDRESS)
    assert len(transport.calls) == 1 and sleeps == []


# --- the errors ---------------------------------------------------------------------------------------------

def test_error_classes_carry_a_status_and_a_safe_message():
    assert issubclass(GeocoderUnavailableError, DataSourceError) and GeocoderUnavailableError.status_code == 503
    assert issubclass(GeocoderResponseError, DataSourceError) and GeocoderResponseError.status_code == 502
    for error in (GeocoderUnavailableError, GeocoderResponseError):
        assert "exact coordinates" in error.public_message and "census" not in error.public_message.lower()
        assert not issubclass(error, AddressNotFoundError)


def test_the_public_message_does_not_leak_the_url_or_the_address():
    with pytest.raises(GeocoderUnavailableError) as info:
        make(FakeTransport((503, b"")), max_retries=0).geocode("77 Secret Ln, Hidden, CA 90000")
    assert "Secret" not in info.value.public_message and "geocoding.geo" not in info.value.public_message


# --- logging ---------------------------------------------------------------------------------------------------

def test_lookups_are_logged_without_the_address(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.geocoding.census_geocoder"):
        make().geocode("77 Secret Ln, Hidden, CA 90000")
    assert "geocode source=census result=match matches=1" in caplog.text
    assert "Secret" not in caplog.text and "Hidden" not in caplog.text


def test_no_match_is_logged_without_the_address(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.geocoding.census_geocoder"):
        with pytest.raises(AddressNotFoundError):
            make(FakeTransport((200, census_body()))).geocode("77 Secret Ln, Hidden, CA 90000")
    assert "result=no_match matches=0" in caplog.text and "Secret" not in caplog.text


def test_retries_are_logged_without_the_address(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.geocoding.census_geocoder"):
        make(FakeTransport((503, b""), (200, SILVER_HILL))).geocode("77 Secret Ln, Hidden, CA 90000")
    assert "attempt 1 failed" in caplog.text and "Secret" not in caplog.text


# --- settings -----------------------------------------------------------------------------------------------------

def test_default_settings():
    s = CensusSettings()
    assert (s.timeout_seconds, s.max_retries, s.retry_delay_seconds, s.benchmark) == (10.0, 1, 0.5, "Public_AR_Current")
    assert s.base_url.startswith("https://geocoding.geo.census.gov/")


def test_settings_from_env_defaults_and_overrides():
    assert CensusSettings.from_env({}) == CensusSettings()
    s = CensusSettings.from_env({"GEOCODER_TIMEOUT_SECONDS": "3", "GEOCODER_MAX_RETRIES": "2", "GEOCODER_RETRY_DELAY_SECONDS": "0"})
    assert (s.timeout_seconds, s.max_retries, s.retry_delay_seconds) == (3.0, 2, 0.0)


@pytest.mark.parametrize(
    "name, value",
    [("GEOCODER_TIMEOUT_SECONDS", "0"), ("GEOCODER_TIMEOUT_SECONDS", "61"), ("GEOCODER_TIMEOUT_SECONDS", "abc"),
     ("GEOCODER_MAX_RETRIES", "4"), ("GEOCODER_MAX_RETRIES", "-1"), ("GEOCODER_MAX_RETRIES", "1.5"),
     ("GEOCODER_RETRY_DELAY_SECONDS", "-1"), ("GEOCODER_RETRY_DELAY_SECONDS", "11")],
)
def test_invalid_settings_are_configuration_errors(name, value):
    with pytest.raises(GeocoderConfigurationError, match=name) as info:
        CensusSettings.from_env({name: value})
    assert isinstance(info.value, ProviderConfigurationError)  # the existing handlers already cover it


def test_the_geocoder_builds_from_the_environment_without_a_request(monkeypatch):
    monkeypatch.setenv("GEOCODER_TIMEOUT_SECONDS", "4")
    geocoder = CensusGeocoder()  # the autouse guard fails the test if this touches the network
    assert geocoder._settings.timeout_seconds == 4.0


# --- the real transport, with urlopen replaced ------------------------------------------------------------

class _Response:
    status = 200

    def __init__(self, body=b"{}"):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_urllib_transport_returns_status_and_body(monkeypatch):
    seen = {}

    def fake(request, timeout):
        seen.update(url=request.full_url, agent=request.get_header("User-agent"), timeout=timeout)
        return _Response(b"[1]")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert census_geocoder.urllib_transport("https://x.test/a", {"User-Agent": "ua"}, 4.0) == (200, b"[1]")
    assert seen == {"url": "https://x.test/a", "agent": "ua", "timeout": 4.0}


def test_urllib_transport_returns_http_error_statuses(monkeypatch):
    import io

    def fake(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "Busy", {}, io.BytesIO(b"busy"))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert census_geocoder.urllib_transport("https://x.test/a", {}, 1.0) == (503, b"busy")


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), OSError("refused"), urllib.error.URLError("no route")])
def test_urllib_transport_maps_network_failures_to_unavailable(monkeypatch, failure):
    def fake(request, timeout):
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(GeocoderUnavailableError):
        census_geocoder.urllib_transport("https://x.test/a", {}, 1.0)


def test_the_default_transport_is_the_urllib_one_and_is_looked_up_when_built(monkeypatch):
    assert CensusGeocoder(CensusSettings())._transport is census_geocoder.urllib_transport
    replacement = FakeTransport()
    monkeypatch.setattr(census_geocoder, "urllib_transport", replacement)
    assert CensusGeocoder(CensusSettings())._transport is replacement
