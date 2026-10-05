"""The building type in the valuation_funnel log line, for successful and insufficient-data valuations.
Mock sources only; nothing here reaches the network or RentCast."""
import logging
import re
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SUBJECT_LATITUDE as LAT
from app.config import DEFAULT_SUBJECT_LONGITUDE as LON
from app.main import app
from app.routers.valuation import get_comparable_source
from app.schemas import ValuationRequest
from app.services.data_sources import ComparableDataSource, RentCastComparableSource, RentCastSettings
from app.services.data_sources.rentcast_source import TtlCache
from app.services.search_options import PROPERTY_TYPES
from app.services.valuation_engine import InsufficientDataError, run_valuation

LOGGER = "app.services.valuation_engine"
MILES_PER_DEGREE = 69.05
TYPES = list(PROPERTY_TYPES)  # all, home, condo, apartment
BODY = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400, "latitude": LAT, "longitude": LON}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("a test tried to use the network")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    yield
    app.dependency_overrides.pop(get_comparable_source, None)


class ListSource(ComparableDataSource):
    def __init__(self, count):
        self.count = count

    def get_comparables(self, subject=None):
        return [
            {"address": f"{i} St", "rent": 2000 + i, "latitude": LAT + (0.05 * (i + 1)) / MILES_PER_DEGREE, "longitude": LON,
             "beds": 3, "baths": 2, "sqft": 1400}
            for i in range(self.count)
        ]


def request(**fields):
    return ValuationRequest(**{**BODY, **fields})


def funnel_records(caplog):
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage().startswith("valuation_funnel")]


def tokens(message):
    """{key: value} for the key=value fields of a funnel line."""
    return dict(re.findall(r"(\w+)=(\S+)", message))


def run_ok(caplog, **fields):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = run_valuation(request(**fields), ListSource(12))
    (record,) = funnel_records(caplog)
    return result, record


def run_insufficient(caplog, **fields):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        with pytest.raises(InsufficientDataError):
            run_valuation(request(**fields), ListSource(2))
    (record,) = funnel_records(caplog)
    return record


# --- every building type, successful valuations ------------------------------------------------------------------

@pytest.mark.parametrize("kind", TYPES)
def test_a_successful_valuation_logs_its_building_type(caplog, kind):
    result, record = run_ok(caplog, property_type=kind)
    assert tokens(record.getMessage())["building_type"] == kind
    assert tokens(record.getMessage())["status"] == "ok"
    assert result["search"]["property_type"] == kind  # the response says the same


def test_all_building_types_is_logged_as_all(caplog):
    _, record = run_ok(caplog, property_type="all")
    assert " building_type=all " in record.getMessage()


def test_the_default_building_type_is_all(caplog):
    _, record = run_ok(caplog)  # the request did not say
    assert tokens(record.getMessage())["building_type"] == "all"


# --- every building type, insufficient-data results -----------------------------------------------------------------

@pytest.mark.parametrize("kind", TYPES)
def test_an_insufficient_data_result_logs_its_building_type(caplog, kind):
    record = run_insufficient(caplog, property_type=kind)
    found = tokens(record.getMessage())
    assert found["status"] == "insufficient_data" and found["building_type"] == kind and found["confidence"] == "none"


def test_an_insufficient_result_with_nothing_fetched_still_logs_it(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        with pytest.raises(InsufficientDataError):
            run_valuation(request(property_type="apartment"), ListSource(0))
    (record,) = funnel_records(caplog)
    assert tokens(record.getMessage())["building_type"] == "apartment" and tokens(record.getMessage())["fetched"] == "0"


# --- the line's format ------------------------------------------------------------------------------------------------

OK_LINE = re.compile(
    r"valuation_funnel status=ok fetched=\d+ after_lookback=\d+ after_distance=\d+ after_attributes=\d+ "
    r"after_outliers=\d+ used=\d+ minimum=\d+ confidence=(low|medium|high) building_type=(all|home|condo|apartment) "
    r"radius_miles=\S+ lookback_days=\d+ nearest_miles=\S+ sqft=(given|not_given)"
)
INSUFFICIENT_LINE = re.compile(OK_LINE.pattern.replace("status=ok", "status=insufficient_data").replace("(low|medium|high)", "none"))


def test_the_successful_line_has_the_expected_format(caplog):
    _, record = run_ok(caplog, property_type="home", search_radius_miles=2)
    assert OK_LINE.fullmatch(record.getMessage()), record.getMessage()


def test_the_insufficient_line_has_the_same_format(caplog):
    record = run_insufficient(caplog, property_type="condo")
    assert INSUFFICIENT_LINE.fullmatch(record.getMessage()), record.getMessage()


def test_an_exact_line_for_a_known_search(caplog):
    _, record = run_ok(caplog, property_type="home", search_radius_miles=2.0, lookback_days=90)
    assert record.getMessage() == (
        "valuation_funnel status=ok fetched=12 after_lookback=12 after_distance=12 after_attributes=12 "
        "after_outliers=12 used=12 minimum=3 confidence=high building_type=home radius_miles=2 "
        "lookback_days=90 nearest_miles=0.05 sqft=given"
    )


def test_the_field_sits_with_the_other_search_fields_in_a_stable_order(caplog):
    _, record = run_ok(caplog, property_type="condo")
    keys = [k for k, _ in re.findall(r"(\w+)=(\S+)", record.getMessage())]
    assert keys.index("confidence") < keys.index("building_type") < keys.index("radius_miles") < keys.index("lookback_days")
    assert keys.count("building_type") == 1


def test_the_existing_fields_are_all_still_there(caplog):
    _, record = run_ok(caplog, property_type="home", search_radius_miles=2)
    found = tokens(record.getMessage())
    assert found["radius_miles"] == "2" and found["lookback_days"] == "90" and found["confidence"] == "high" and found["used"] == "12"
    for key in ("status", "fetched", "after_lookback", "after_distance", "after_attributes", "after_outliers",
                "used", "minimum", "confidence", "building_type", "radius_miles", "lookback_days", "nearest_miles", "sqft"):
        assert key in found, key


def test_one_funnel_line_per_valuation(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        run_valuation(request(property_type="home"), ListSource(12))
        with pytest.raises(InsufficientDataError):
            run_valuation(request(property_type="condo"), ListSource(1))
    assert [tokens(r.getMessage())["building_type"] for r in funnel_records(caplog)] == ["home", "condo"]


# --- the structured record -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", TYPES)
def test_the_log_record_carries_the_building_type_as_an_attribute(caplog, kind):
    _, ok = run_ok(caplog, property_type=kind)
    assert ok.building_type == kind and ok.search["property_type"] == kind


def test_the_insufficient_record_carries_it_too(caplog):
    record = run_insufficient(caplog, property_type="apartment")
    assert record.building_type == "apartment" and record.search["property_type"] == "apartment"


# --- from the API and the web form ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", TYPES)
def test_the_api_request_is_logged_with_its_building_type(caplog, kind):
    app.dependency_overrides[get_comparable_source] = lambda: ListSource(12)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        response = TestClient(app).post("/valuation", json={**BODY, "property_type": kind})
    assert response.status_code == 200
    (record,) = funnel_records(caplog)
    assert tokens(record.getMessage())["building_type"] == kind and response.json()["search"]["property_type"] == kind


@pytest.mark.parametrize("kind", TYPES)
def test_an_insufficient_api_request_is_logged_with_its_building_type(caplog, kind):
    app.dependency_overrides[get_comparable_source] = lambda: ListSource(1)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        response = TestClient(app).post("/valuation", json={**BODY, "property_type": kind})
    assert response.status_code == 404
    (record,) = funnel_records(caplog)
    assert tokens(record.getMessage())["building_type"] == kind and tokens(record.getMessage())["status"] == "insufficient_data"


def submit_form(count, kind, caplog):
    app.dependency_overrides[get_comparable_source] = lambda: ListSource(count)
    client = TestClient(app)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/ui/").text).group(1)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        response = client.post("/ui/valuation", data={
            "address": "123 Main St", "beds": "3", "baths": "2", "sqft": "1400", "latitude": str(LAT), "longitude": str(LON),
            "property_type": kind, "csrf_token": token,
        })
    assert response.status_code == 200
    return response


@pytest.mark.parametrize("kind", TYPES)
def test_the_web_form_choice_is_logged_for_a_valuation(caplog, kind):
    response = submit_form(12, kind, caplog)
    assert 'id="result-card"' in response.text
    (record,) = funnel_records(caplog)
    assert tokens(record.getMessage())["building_type"] == kind


@pytest.mark.parametrize("kind", TYPES)
def test_the_web_form_choice_is_logged_when_there_is_not_enough_data(caplog, kind):
    response = submit_form(1, kind, caplog)
    assert 'id="insufficient-data"' in response.text
    (record,) = funnel_records(caplog)
    assert tokens(record.getMessage())["building_type"] == kind


def test_an_older_form_that_posts_no_building_type_logs_all(caplog):
    app.dependency_overrides[get_comparable_source] = lambda: ListSource(12)
    client = TestClient(app)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/ui/").text).group(1)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        client.post("/ui/valuation", data={"address": "x", "beds": "3", "baths": "2", "sqft": "1400",
                                           "latitude": str(LAT), "longitude": str(LON), "csrf_token": token})
    (record,) = funnel_records(caplog)
    assert tokens(record.getMessage())["building_type"] == "all"


# --- the logged type is the one sent to RentCast -----------------------------------------------------------------------

@pytest.mark.parametrize("kind, sent", [("all", None), ("home", "Single Family"), ("condo", "Condo"), ("apartment", "Apartment")])
def test_the_logged_building_type_matches_the_rentcast_query(caplog, kind, sent):
    import json
    import urllib.parse

    queries = []

    def transport(url, headers, timeout):
        queries.append(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)))
        listings = [{"formattedAddress": f"{i} Oak St", "price": 2000 + i, "latitude": LAT + 0.0007 * (i + 1), "longitude": LON,
                     "bedrooms": 3, "bathrooms": 2, "squareFootage": 1400, "daysOnMarket": 10} for i in range(5)]
        return 200, json.dumps(listings).encode()

    source = RentCastComparableSource(RentCastSettings(api_key="k"), transport=transport, cache=TtlCache(), sleep=lambda s: None)
    app.dependency_overrides[get_comparable_source] = lambda: source
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert TestClient(app).post("/valuation", json={**BODY, "property_type": kind}).status_code == 200
    (record,) = funnel_records(caplog)
    assert tokens(record.getMessage())["building_type"] == kind
    assert queries[0].get("propertyType") == sent


# --- what must not be in the line -------------------------------------------------------------------------------------

def test_the_funnel_line_still_contains_no_address(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        run_valuation(request(address="742 Evergreen Terrace", property_type="home"), ListSource(12))
    assert "Evergreen" not in caplog.text and "742" not in funnel_records(caplog)[0].getMessage()


def test_only_the_four_known_values_can_appear(caplog):
    seen = set()
    for kind in TYPES:
        caplog.clear()
        _, record = run_ok(caplog, property_type=kind)
        seen.add(tokens(record.getMessage())["building_type"])
    assert seen == {"all", "home", "condo", "apartment"}
