"""Search choices travelling from the request to the RentCast query, the cache keys, listing ages and
the new logging. The RentCast source uses a fake transport; nothing here reaches the network."""
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SUBJECT_LATITUDE as LAT
from app.config import DEFAULT_SUBJECT_LONGITUDE as LON
from app.main import app
from app.routers.valuation import get_comparable_source
from app.schemas import ValuationRequest
from app.services.data_sources import (
    MockComparableSource,
    ProviderConfigurationError,
    RentCastComparableSource,
    RentCastSettings,
    SubjectProperty,
)
from app.services.data_sources.rentcast_source import TtlCache, _days_on_market
from app.services.valuation_engine import run_valuation

SUBJECT = SubjectProperty("1 A St", LAT, LON, 3, 2.0, 1400)
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
ROOT_LOGGER = "app.services.data_sources.rentcast_source"
MILES_PER_DEGREE = 69.05


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("a test tried to use the network")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    for name in ("DATA_PROVIDER", "RENTCAST_API_KEY", "RENTCAST_LIMIT", "RENTCAST_RADIUS_MILES"):
        monkeypatch.delenv(name, raising=False)


def listing(i=0, miles=0.5, **overrides):
    base = {
        "formattedAddress": f"{i} Oak St, La Mesa, CA", "price": 2000 + i, "latitude": LAT + miles / MILES_PER_DEGREE,
        "longitude": LON, "bedrooms": 3, "bathrooms": 2, "squareFootage": 1400, "daysOnMarket": 20,
    }
    base.update(overrides)
    return base


class FakeTransport:
    def __init__(self, listings=None):
        self.listings = [listing()] if listings is None else listings
        self.urls = []

    def __call__(self, url, headers, timeout):
        import json

        self.urls.append(url)
        return 200, json.dumps(self.listings).encode()

    def query(self, index=-1):
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.urls[index]).query).items()}


def source(transport, cache=None, **settings):
    return RentCastComparableSource(
        RentCastSettings(api_key="secret-key", **settings), transport=transport,
        cache=cache if cache is not None else TtlCache(), sleep=lambda s: None,
    )


# --- the radius sent to RentCast is the radius chosen -------------------------------------------------------------

@pytest.mark.parametrize("radius, sent", [(0.5, "0.5"), (1.0, "1"), (2.0, "2"), (3.0, "3"), (5.0, "5")])
def test_the_query_radius_is_the_chosen_search_radius(radius, sent):
    transport = FakeTransport()
    source(transport).get_comparables(replace(SUBJECT, search_radius_miles=radius))
    assert transport.query()["radius"] == sent


def test_the_default_query_radius_is_one_mile_not_five():
    transport = FakeTransport()
    source(transport).get_comparables(SUBJECT)
    assert transport.query()["radius"] == "1"


def test_the_default_limit_is_five_hundred():
    transport = FakeTransport()
    source(transport).get_comparables(SUBJECT)
    assert transport.query()["limit"] == "500" and RentCastSettings(api_key="k").limit == 500


def test_the_limit_can_still_be_lowered(monkeypatch):
    transport = FakeTransport()
    source(transport, limit=50).get_comparables(SUBJECT)
    assert transport.query()["limit"] == "50"
    s = RentCastSettings.from_env({"RENTCAST_API_KEY": "k", "RENTCAST_LIMIT": "100"})
    assert s.limit == 100


def test_there_is_no_radius_setting_any_more():
    assert not hasattr(RentCastSettings(api_key="k"), "radius_miles")
    # an old environment variable is ignored, not an error
    RentCastSettings.from_env({"RENTCAST_API_KEY": "k", "RENTCAST_RADIUS_MILES": "banana"})


def test_the_query_has_the_expected_parameters_and_nothing_else():
    transport = FakeTransport()
    source(transport).get_comparables(replace(SUBJECT, search_radius_miles=2.0))
    assert transport.query() == {"latitude": "32.76780", "longitude": "-117.02310", "radius": "2", "limit": "500", "status": "Active"}


def test_the_lookback_is_never_sent_to_rentcast():
    """RentCast's daysOld filter takes ranges with undocumented syntax; the window is applied afterwards."""
    for days in (30, 90, 180, 365):
        transport = FakeTransport()
        source(transport).get_comparables(replace(SUBJECT, lookback_days=days))
        assert "daysOld" not in transport.query() and not any("old" in k.lower() for k in transport.query())


# --- building type ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind, sent", [("home", "Single Family"), ("condo", "Condo"), ("apartment", "Apartment")])
def test_the_building_type_is_sent_as_rentcasts_property_type(kind, sent):
    transport = FakeTransport()
    source(transport).get_comparables(replace(SUBJECT, property_type=kind))
    assert transport.query()["propertyType"] == sent
    assert f"propertyType={urllib.parse.quote_plus(sent)}" in transport.urls[0]


def test_all_building_types_sends_no_filter():
    transport = FakeTransport()
    source(transport).get_comparables(replace(SUBJECT, property_type="all"))
    assert "propertyType" not in transport.query()


@pytest.mark.parametrize("kind", ["house", "Condo", "", "townhouse"])
def test_an_unknown_building_type_is_refused_before_any_request(kind):
    transport = FakeTransport()
    with pytest.raises(ValueError, match="property type"):
        source(transport).get_comparables(replace(SUBJECT, property_type=kind))
    assert transport.urls == []


# --- the cache -------------------------------------------------------------------------------------------------------------

def test_a_different_radius_is_a_different_request():
    transport, cache = FakeTransport(), TtlCache()
    s = source(transport, cache)
    s.get_comparables(replace(SUBJECT, search_radius_miles=1.0))
    s.get_comparables(replace(SUBJECT, search_radius_miles=2.0))
    s.get_comparables(replace(SUBJECT, search_radius_miles=1.0))  # cached
    assert len(transport.urls) == 2


def test_a_different_building_type_is_a_different_request():
    transport, cache = FakeTransport(), TtlCache()
    s = source(transport, cache)
    s.get_comparables(replace(SUBJECT, property_type="all"))
    s.get_comparables(replace(SUBJECT, property_type="condo"))
    s.get_comparables(replace(SUBJECT, property_type="condo"))
    assert len(transport.urls) == 2


def test_a_different_lookback_reuses_the_cached_listings():
    transport, cache = FakeTransport(), TtlCache()
    s = source(transport, cache)
    for days in (30, 90, 180, 365):
        s.get_comparables(replace(SUBJECT, lookback_days=days))
    assert len(transport.urls) == 1  # changing the window costs no new RentCast request


def test_the_lookback_changes_the_result_without_a_new_request():
    transport, cache = FakeTransport([listing(i, miles=0.1 * (i + 1), daysOnMarket=age) for i, age in enumerate([10, 50, 120, 250])]), TtlCache()
    s = source(transport, cache)

    def used(days):
        return run_valuation(ValuationRequest(address="x", beds=3, baths=2, sqft=1400, latitude=LAT, longitude=LON, lookback_days=days), s, min_comparables=1)["comparable_count"]

    assert [used(d) for d in (30, 90, 180, 365)] == [1, 2, 3, 4]
    assert len(transport.urls) == 1


def test_a_different_limit_is_a_different_request():
    transport, cache = FakeTransport(), TtlCache()
    source(transport, cache, limit=500).get_comparables(SUBJECT)
    source(transport, cache, limit=100).get_comparables(SUBJECT)
    assert len(transport.urls) == 2


# --- listing age ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"daysOnMarket": 12}, 12), ({"daysOnMarket": 12.7}, 12), ({"daysOnMarket": 0}, 0),
        ({"listedDate": "2026-09-24T12:00:00.000Z"}, 10), ({"listedDate": "2026-09-24T12:00:00+00:00"}, 10),
        ({"listedDate": "2026-09-24T00:00:00"}, 10),  # no time zone: taken as UTC
        ({"listedDate": "2026-10-05T00:00:00Z"}, 0),  # in the future: not negative
        ({"daysOnMarket": 5, "listedDate": "2026-01-01T00:00:00Z"}, 5),  # daysOnMarket wins
        ({"daysOnMarket": -3, "listedDate": "2026-09-24T12:00:00Z"}, 10),  # unusable number: use the date
        ({"daysOnMarket": "12"}, None), ({"daysOnMarket": True}, None), ({"daysOnMarket": None}, None),
        ({"listedDate": "not a date"}, None), ({"listedDate": 12345}, None), ({}, None),
    ],
)
def test_days_on_market(fields, expected):
    assert _days_on_market(fields, NOW) == expected


def test_listings_carry_their_age_into_the_comparables():
    transport = FakeTransport([listing(1, daysOnMarket=33), listing(2, daysOnMarket=None, listedDate=(datetime.now(timezone.utc) - timedelta(days=7)).isoformat())])
    ages = [c["days_on_market"] for c in source(transport).get_comparables(SUBJECT)]
    assert ages[0] == 33 and ages[1] in (6, 7)


def test_a_listing_without_any_age_is_kept_with_an_unknown_age():
    transport = FakeTransport([listing(1, daysOnMarket=None)])
    assert source(transport).get_comparables(SUBJECT)[0]["days_on_market"] is None


def test_the_sample_data_ignores_the_search_choices_that_do_not_apply_to_it():
    mock = MockComparableSource()
    plain = mock.get_comparables(SUBJECT)
    assert mock.get_comparables(replace(SUBJECT, lookback_days=30, property_type="condo", search_radius_miles=0.5)) == plain


# --- the choices travel from the request to the RentCast query ---------------------------------------------------------

def rentcast_with(transport):
    return RentCastComparableSource(RentCastSettings(api_key="k"), transport=transport, cache=TtlCache(), sleep=lambda s: None)


@pytest.fixture
def override():
    holder = {}

    def use(src):
        app.dependency_overrides[get_comparable_source] = lambda: src

    yield use
    app.dependency_overrides.pop(get_comparable_source, None)


def test_the_api_request_reaches_the_rentcast_query(override):
    transport = FakeTransport([listing(i, miles=0.4 + 0.1 * i) for i in range(4)])
    override(rentcast_with(transport))
    body = {"address": "x", "beds": 3, "baths": 2, "latitude": LAT, "longitude": LON,
            "search_radius_miles": 2, "lookback_days": 180, "property_type": "condo"}
    assert TestClient(app).post("/valuation", json=body).status_code == 200
    assert transport.query() == {"latitude": "32.76780", "longitude": "-117.02310", "radius": "2", "limit": "500",
                                 "status": "Active", "propertyType": "Condo"}


def test_the_form_choices_reach_the_rentcast_query(override):
    transport = FakeTransport([listing(i, miles=0.2 + 0.1 * i) for i in range(4)])
    override(rentcast_with(transport))
    client = TestClient(app)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/ui/").text).group(1)
    response = client.post("/ui/valuation", data={
        "address": "1 A St", "beds": "3", "baths": "2", "sqft": "", "latitude": str(LAT), "longitude": str(LON),
        "search_radius_miles": "3", "lookback_days": "365", "property_type": "apartment", "csrf_token": token,
    })
    assert response.status_code == 200
    assert transport.query()["radius"] == "3" and transport.query()["propertyType"] == "Apartment"
    assert 'id="confidence-notes"' in response.text  # square feet were left blank


def test_the_form_defaults_reach_the_query_when_nothing_is_changed(override):
    transport = FakeTransport([listing(i, miles=0.2 + 0.1 * i) for i in range(4)])
    override(rentcast_with(transport))
    client = TestClient(app)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/ui/").text).group(1)
    client.post("/ui/valuation", data={
        "address": "1 A St", "beds": "3", "baths": "2", "sqft": "1400", "latitude": str(LAT), "longitude": str(LON), "csrf_token": token,
    })  # an older page that does not post the new fields
    assert transport.query()["radius"] == "1" and "propertyType" not in transport.query()


# --- logging -----------------------------------------------------------------------------------------------------------------

def rentcast_lines(caplog):
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == ROOT_LOGGER]


def test_the_request_is_logged_with_radius_limit_and_nearest_listing(caplog):
    transport = FakeTransport([listing(1, miles=0.8), listing(2, miles=0.3), listing(3, miles=2.0)])
    with caplog.at_level(logging.INFO, logger=ROOT_LOGGER):
        source(transport).get_comparables(replace(SUBJECT, search_radius_miles=2.0))
    (level, message), = [x for x in rentcast_lines(caplog) if "listings in" in x[1]]
    assert level == "INFO"
    assert re.fullmatch(r"RentCast: 3 listings in \d+ ms \(radius 2 miles, limit 500, nearest 0\.30 miles\)", message), message


def test_the_nearest_listing_is_none_when_rentcast_returns_nothing(caplog):
    with caplog.at_level(logging.INFO, logger=ROOT_LOGGER):
        source(FakeTransport([])).get_comparables(SUBJECT)
    (_, message), = [x for x in rentcast_lines(caplog) if "listings in" in x[1]]
    assert re.fullmatch(r"RentCast: 0 listings in \d+ ms \(radius 1 mile, limit 500, nearest none\)", message), message


@pytest.mark.parametrize("limit", [100, 500])
def test_a_response_that_reaches_the_limit_warns(caplog, limit):
    transport = FakeTransport([listing(i, miles=0.5) for i in range(limit)])
    with caplog.at_level(logging.INFO, logger=ROOT_LOGGER):
        source(transport, limit=limit).get_comparables(SUBJECT)
    warnings = [m for level, m in rentcast_lines(caplog) if level == "WARNING"]
    assert warnings == [f"RentCast response reached limit ({limit}); nearby listings may be missing."]


def test_the_default_limit_warning_says_500(caplog):
    transport = FakeTransport([listing(i) for i in range(500)])
    with caplog.at_level(logging.WARNING, logger=ROOT_LOGGER):
        source(transport).get_comparables(SUBJECT)
    assert "RentCast response reached limit (500); nearby listings may be missing." in caplog.text


def test_no_warning_below_the_limit(caplog):
    with caplog.at_level(logging.INFO, logger=ROOT_LOGGER):
        source(FakeTransport([listing(i) for i in range(499)])).get_comparables(SUBJECT)
    assert [m for level, m in rentcast_lines(caplog) if level == "WARNING"] == []


def test_the_cap_counts_listings_rentcast_returned_even_if_some_are_unusable(caplog):
    listings = [listing(i) for i in range(5)] + [listing(9, price=None), listing(10, latitude=None)]
    with caplog.at_level(logging.INFO, logger=ROOT_LOGGER):
        source(FakeTransport(listings), limit=7).get_comparables(SUBJECT)
    warnings = [m for level, m in rentcast_lines(caplog) if level == "WARNING"]
    assert "RentCast response reached limit (7); nearby listings may be missing." in warnings
    assert any("skipped 2 of 7" in m for m in warnings)


def test_a_cached_answer_logs_no_request_and_no_warning(caplog):
    transport, cache = FakeTransport([listing(i) for i in range(3)]), TtlCache()
    s = source(transport, cache, limit=3)
    s.get_comparables(SUBJECT)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=ROOT_LOGGER):
        s.get_comparables(SUBJECT)
    assert rentcast_lines(caplog) == [("INFO", "RentCast: cache hit, no API call")]


def test_the_funnel_line_records_the_search(caplog):
    class Source:
        def get_comparables(self, subject=None):
            return [dict(address="a", rent=2000, latitude=LAT + m / MILES_PER_DEGREE, longitude=LON, beds=3, baths=2, sqft=1400) for m in (0.3, 0.6)]

    with caplog.at_level(logging.INFO, logger="app.services.valuation_engine"):
        run_valuation(ValuationRequest(address="x", beds=3, baths=2, latitude=LAT, longitude=LON, search_radius_miles=2, lookback_days=180), Source(), min_comparables=1)
    (record,) = [r for r in caplog.records if r.getMessage().startswith("valuation_funnel")]
    assert "radius_miles=2 lookback_days=180 nearest_miles=0.30 sqft=not_given" in record.getMessage()
    assert record.search["radius_miles"] == 2.0 and record.search["nearest_listing_miles"] == 0.3


def test_nothing_secret_or_personal_is_logged(caplog):
    with caplog.at_level(logging.DEBUG):
        source(FakeTransport([listing(1, formattedAddress="77 Secret Ln, Hidden, CA")])).get_comparables(SUBJECT)
    assert "secret-key" not in caplog.text and "Secret" not in caplog.text


def test_env_example_no_longer_offers_a_radius_setting():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
    assert "RENTCAST_LIMIT=500" in text
    assert not re.search(r"^#?\s*RENTCAST_RADIUS_MILES=", text, re.M)
