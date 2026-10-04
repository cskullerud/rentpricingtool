import json
import urllib.parse

import pytest

from app.services.data_sources import (
    ComparableDataSource,
    DataSourceError,
    ProviderConfigurationError,
    RentCastAuthError,
    RentCastComparableSource,
    RentCastRateLimitError,
    RentCastResponseError,
    RentCastSettings,
    RentCastUnavailableError,
    SubjectProperty,
)
from app.services.data_sources.rentcast_source import TtlCache

SUBJECT = SubjectProperty("1 A St", 32.7678, -117.0231, 3, 2.0, 1400)


def listing(**overrides):
    base = {
        "id": "x", "formattedAddress": "10 Oak St, La Mesa, CA 91942", "price": 2450,
        "latitude": 32.77, "longitude": -117.02, "bedrooms": 3, "bathrooms": 2.5,
        "squareFootage": 1350, "propertyType": "Single Family", "status": "Active",
    }
    base.update(overrides)
    return base


class FakeTransport:
    def __init__(self, status=200, payload=None, body=None):
        self.status = status
        self.body = body if body is not None else json.dumps([listing()] if payload is None else payload).encode()
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        return self.status, self.body


def make(transport, **settings):
    return RentCastComparableSource(
        RentCastSettings(api_key="secret-key", **settings), transport=transport, cache=TtlCache()
    )


def test_is_a_comparable_data_source():
    assert isinstance(make(FakeTransport()), ComparableDataSource)


def test_maps_a_listing_to_a_comparable():
    comps = make(FakeTransport()).get_comparables(SUBJECT)
    assert comps == [{
        "address": "10 Oak St, La Mesa, CA 91942", "rent": 2450, "latitude": 32.77,
        "longitude": -117.02, "beds": 3, "baths": 2.5, "sqft": 1350,
    }]


def test_rounds_and_converts_numbers():
    payload = [listing(price=2449.6, bedrooms=2.0, squareFootage=1200.0, bathrooms=1)]
    comp = make(FakeTransport(payload=payload)).get_comparables(SUBJECT)[0]
    assert (comp["rent"], comp["beds"], comp["sqft"], comp["baths"]) == (2450, 2, 1200, 1.0)
    assert isinstance(comp["beds"], int) and isinstance(comp["sqft"], int)


def test_a_studio_has_zero_beds():
    comp = make(FakeTransport(payload=[listing(bedrooms=0)])).get_comparables(SUBJECT)[0]
    assert comp["beds"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"price": None}, {"price": "2450"}, {"price": 0}, {"price": True},
        {"formattedAddress": None}, {"formattedAddress": "  "},
        {"latitude": None}, {"longitude": 500}, {"latitude": 91},
        {"bedrooms": None}, {"bathrooms": None}, {"bathrooms": -1},
        {"squareFootage": None}, {"squareFootage": 0},
    ],
)
def test_incomplete_or_invalid_listings_are_skipped(bad):
    good = listing(formattedAddress="Good St")
    comps = make(FakeTransport(payload=[listing(**bad), good])).get_comparables(SUBJECT)
    assert [c["address"] for c in comps] == ["Good St"]


def test_non_object_entries_are_skipped():
    assert make(FakeTransport(payload=[None, 5, "x", listing()])).get_comparables(SUBJECT)[0]["rent"] == 2450


def test_empty_result_is_an_empty_list():
    assert make(FakeTransport(payload=[])).get_comparables(SUBJECT) == []


def test_request_url_headers_and_timeout():
    transport = FakeTransport()
    make(transport, radius_miles=3, limit=50, timeout_seconds=7, base_url="https://example.test").get_comparables(SUBJECT)
    url, headers, timeout = transport.calls[0]
    parsed = urllib.parse.urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://example.test/v1/listings/rental/long-term"
    assert dict(urllib.parse.parse_qsl(parsed.query)) == {
        "latitude": "32.76780", "longitude": "-117.02310", "radius": "3", "limit": "50", "status": "Active",
    }
    assert headers["X-Api-Key"] == "secret-key"
    assert timeout == 7


def test_requires_a_subject():
    transport = FakeTransport()
    with pytest.raises(ValueError, match="location"):
        make(transport).get_comparables()
    assert transport.calls == []


@pytest.mark.parametrize(
    "status, error",
    [
        (401, RentCastAuthError), (403, RentCastAuthError), (429, RentCastRateLimitError),
        (500, RentCastUnavailableError), (503, RentCastUnavailableError),
        (400, RentCastResponseError), (404, RentCastResponseError),
    ],
)
def test_http_errors_map_to_specific_errors(status, error):
    with pytest.raises(error) as info:
        make(FakeTransport(status=status, body=b"{}")).get_comparables(SUBJECT)
    assert isinstance(info.value, DataSourceError)
    assert "secret-key" not in str(info.value)


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"a": 1}', b"5"])
def test_bad_bodies_raise_a_response_error(body):
    with pytest.raises(RentCastResponseError):
        make(FakeTransport(body=body)).get_comparables(SUBJECT)


def test_transport_failures_propagate():
    def down(url, headers, timeout):
        raise RentCastUnavailableError("down")

    with pytest.raises(RentCastUnavailableError):
        make(down).get_comparables(SUBJECT)


def test_second_lookup_for_the_same_area_is_cached():
    transport = FakeTransport()
    source = make(transport)
    first = source.get_comparables(SUBJECT)
    nearby = SubjectProperty("2 B St", 32.76781, -117.02309, 2, 1.0, 900)
    assert source.get_comparables(nearby) == first
    assert len(transport.calls) == 1


def test_a_different_area_is_not_served_from_cache():
    transport = FakeTransport()
    source = make(transport)
    source.get_comparables(SUBJECT)
    source.get_comparables(SubjectProperty("far", 33.5, -117.5, 3, 2, 1400))
    assert len(transport.calls) == 2


def test_cached_results_cannot_be_modified_by_callers():
    source = make(FakeTransport())
    source.get_comparables(SUBJECT)[0]["rent"] = -1
    assert source.get_comparables(SUBJECT)[0]["rent"] == 2450


def test_cache_entries_expire():
    now = [0.0]
    transport = FakeTransport()
    source = RentCastComparableSource(
        RentCastSettings(api_key="k", cache_ttl_seconds=60),
        transport=transport, cache=TtlCache(clock=lambda: now[0]),
    )
    source.get_comparables(SUBJECT)
    now[0] = 59
    source.get_comparables(SUBJECT)
    assert len(transport.calls) == 1
    now[0] = 61
    source.get_comparables(SUBJECT)
    assert len(transport.calls) == 2


def test_ttl_zero_disables_caching():
    transport = FakeTransport()
    source = make(transport, cache_ttl_seconds=0)
    source.get_comparables(SUBJECT)
    source.get_comparables(SUBJECT)
    assert len(transport.calls) == 2


def test_failures_are_not_cached():
    cache = TtlCache()
    failing = FakeTransport(status=500, body=b"")
    settings = RentCastSettings(api_key="k")
    with pytest.raises(RentCastUnavailableError):
        RentCastComparableSource(settings, transport=failing, cache=cache).get_comparables(SUBJECT)
    working = FakeTransport()
    assert RentCastComparableSource(settings, transport=working, cache=cache).get_comparables(SUBJECT)


# --- settings --------------------------------------------------------------------------

def test_settings_from_env_defaults():
    s = RentCastSettings.from_env({"RENTCAST_API_KEY": " k "})
    assert (s.api_key, s.base_url, s.radius_miles, s.limit) == ("k", "https://api.rentcast.io", 5.0, 100)
    assert (s.timeout_seconds, s.cache_ttl_seconds) == (10.0, 86400.0)


def test_settings_from_env_overrides():
    s = RentCastSettings.from_env({
        "RENTCAST_API_KEY": "k", "RENTCAST_BASE_URL": "https://x.test/", "RENTCAST_RADIUS_MILES": "2.5",
        "RENTCAST_LIMIT": "25", "RENTCAST_TIMEOUT_SECONDS": "3", "RENTCAST_CACHE_TTL_SECONDS": "0",
    })
    assert (s.base_url, s.radius_miles, s.limit, s.timeout_seconds, s.cache_ttl_seconds) == ("https://x.test", 2.5, 25, 3.0, 0.0)


@pytest.mark.parametrize("env", [{}, {"RENTCAST_API_KEY": "  "}])
def test_missing_key_is_a_configuration_error(env):
    with pytest.raises(ProviderConfigurationError, match="RENTCAST_API_KEY"):
        RentCastSettings.from_env(env)


@pytest.mark.parametrize(
    "name, value",
    [("RENTCAST_RADIUS_MILES", "abc"), ("RENTCAST_RADIUS_MILES", "500"), ("RENTCAST_LIMIT", "0"),
     ("RENTCAST_LIMIT", "501"), ("RENTCAST_LIMIT", "1.5"), ("RENTCAST_TIMEOUT_SECONDS", "0"),
     ("RENTCAST_CACHE_TTL_SECONDS", "-1")],
)
def test_invalid_numbers_are_configuration_errors(name, value):
    with pytest.raises(ProviderConfigurationError, match=name):
        RentCastSettings.from_env({"RENTCAST_API_KEY": "k", name: value})


def test_the_api_key_is_not_in_the_repr():
    assert "secret-key" not in repr(RentCastSettings(api_key="secret-key"))


def test_source_reads_settings_from_the_environment(monkeypatch):
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError):
        RentCastComparableSource()
    monkeypatch.setenv("RENTCAST_API_KEY", "k")
    assert isinstance(RentCastComparableSource(), RentCastComparableSource)


def test_engine_prices_from_rentcast_listings():
    from app.schemas import ValuationRequest
    from app.services.valuation_engine import run_valuation

    payload = [
        listing(formattedAddress=f"{i} Oak St", price=price, latitude=32.7678, longitude=-117.0231,
                bedrooms=3, bathrooms=2, squareFootage=1400)
        for i, price in enumerate([2000, 2200, 2400, 2600])
    ]
    request = ValuationRequest(address="9 Elm St", beds=3, baths=2, sqft=1400, latitude=32.7678, longitude=-117.0231)
    result = run_valuation(request, make(FakeTransport(payload=payload)))
    assert result["comparable_count"] == 4
    assert result["median"] == 2300


# --- the real transport, with urlopen patched out (no network) -------------------------

class _Response:
    status = 200

    def __init__(self, body=b"[]"):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_urllib_transport_returns_status_and_body(monkeypatch):
    import urllib.request
    from app.services.data_sources.rentcast_source import urllib_transport

    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"], seen["key"], seen["timeout"] = request.full_url, request.get_header("X-api-key"), timeout
        return _Response(b"[1]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert urllib_transport("https://x.test/a", {"X-Api-Key": "k"}, 4.0) == (200, b"[1]")
    assert seen == {"url": "https://x.test/a", "key": "k", "timeout": 4.0}


def test_urllib_transport_returns_http_error_statuses(monkeypatch):
    import io
    import urllib.error
    import urllib.request
    from app.services.data_sources.rentcast_source import urllib_transport

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {}, io.BytesIO(b"slow down"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert urllib_transport("https://x.test/a", {}, 1.0) == (429, b"slow down")


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), OSError("refused")])
def test_urllib_transport_maps_network_failures(monkeypatch, failure):
    import urllib.request
    from app.services.data_sources.rentcast_source import urllib_transport

    def fake_urlopen(request, timeout):
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RentCastUnavailableError):
        urllib_transport("https://x.test/a", {}, 1.0)
