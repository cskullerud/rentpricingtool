"""Search controls at the engine and API level: radius, lookback, building type and optional square
feet. Mock sources only; no network."""
import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SUBJECT_LATITUDE as LAT
from app.config import DEFAULT_SUBJECT_LONGITUDE as LON
from app.main import app
from app.routers.valuation import get_comparable_source
from app.schemas import ValuationRequest
from app.services import comparables as comps
from app.services import quality
from app.services.data_sources import ComparableDataSource, MockComparableSource
from app.services.search_options import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PROPERTY_TYPE,
    DEFAULT_RADIUS_MILES,
    LOOKBACK_OPTIONS_DAYS,
    PROPERTY_TYPES,
    RADIUS_OPTIONS_MILES,
    format_days,
    format_miles,
    property_type_label,
    rentcast_property_type,
)
from app.services.valuation_engine import InsufficientDataError, run_valuation

MILES_PER_DEGREE_OF_LATITUDE = 69.05
BODY = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400, "latitude": LAT, "longitude": LON}
client = TestClient(app)


def at(miles, rent=2000, **overrides):
    """A comparable that matches the subject, `miles` due north of it."""
    base = {
        "address": f"{miles} mi St", "rent": rent, "latitude": LAT + miles / MILES_PER_DEGREE_OF_LATITUDE,
        "longitude": LON, "beds": 3, "baths": 2, "sqft": 1400,
    }
    base.update(overrides)
    return base


class ListSource(ComparableDataSource):
    def __init__(self, comparables):
        self.comparables = list(comparables)
        self.subjects = []

    def get_comparables(self, subject=None):
        self.subjects.append(subject)
        return [dict(c) for c in self.comparables]


def request(**fields):
    return ValuationRequest(**{**BODY, **fields})


def search(result_or_error):
    return result_or_error["search"] if isinstance(result_or_error, dict) else result_or_error.search


# --- the options themselves ---------------------------------------------------------------------------------

def test_the_offered_choices():
    assert RADIUS_OPTIONS_MILES == (0.5, 1.0, 2.0, 3.0, 5.0) and DEFAULT_RADIUS_MILES == 1.0
    assert LOOKBACK_OPTIONS_DAYS == (30, 90, 180, 365) and DEFAULT_LOOKBACK_DAYS == 90
    assert list(PROPERTY_TYPES) == ["all", "home", "condo", "apartment"] and DEFAULT_PROPERTY_TYPE == "all"


@pytest.mark.parametrize("miles, text", [(0.5, "0.5 miles"), (1.0, "1 mile"), (2.0, "2 miles"), (3, "3 miles"), (5.0, "5 miles")])
def test_distances_always_show_their_unit(miles, text):
    assert format_miles(miles) == text


@pytest.mark.parametrize("days, text", [(30, "30 days"), (90, "90 days"), (1, "1 day"), (365, "365 days")])
def test_day_labels(days, text):
    assert format_days(days) == text


@pytest.mark.parametrize(
    "key, label, rentcast",
    [("all", "All building types", None), ("home", "Home (single-family)", "Single Family"),
     ("condo", "Condo", "Condo"), ("apartment", "Apartment", "Apartment")],
)
def test_building_types_map_to_rentcast_values(key, label, rentcast):
    assert property_type_label(key) == label and rentcast_property_type(key) == rentcast


def test_the_rentcast_values_are_ones_rentcast_documents():
    documented = {"Single Family", "Condo", "Townhouse", "Manufactured", "Multi-Family", "Apartment"}
    assert {v for _, v in PROPERTY_TYPES.values() if v} <= documented


# --- the request ------------------------------------------------------------------------------------------------

def test_the_defaults_keep_older_requests_working():
    r = ValuationRequest(address="1 A St", beds=3, baths=2, sqft=1400)
    assert (r.search_radius_miles, r.lookback_days, r.property_type) == (1.0, 90, "all")


def test_square_feet_may_be_left_out():
    r = ValuationRequest(address="1 A St", beds=3, baths=2)
    assert r.sqft is None


@pytest.mark.parametrize("radius", [0.5, 1, 2, 3, 5, 1.0, "2", "0.5"])
def test_every_offered_radius_is_accepted(radius):
    assert request(search_radius_miles=radius).search_radius_miles in RADIUS_OPTIONS_MILES


@pytest.mark.parametrize("radius", [0, -1, 0.25, 4, 10, 1.5, "abc", None, True, False])
def test_other_radii_are_rejected(radius):
    with pytest.raises(ValueError):
        request(search_radius_miles=radius)


@pytest.mark.parametrize("days", [30, 90, 180, 365, "90"])
def test_every_offered_lookback_is_accepted(days):
    assert request(lookback_days=days).lookback_days in LOOKBACK_OPTIONS_DAYS


@pytest.mark.parametrize("days", [0, 1, 45, 366, -90, "abc", None, True, 90.5])
def test_other_lookbacks_are_rejected(days):
    with pytest.raises(ValueError):
        request(lookback_days=days)


@pytest.mark.parametrize("kind", ["all", "home", "condo", "apartment"])
def test_every_offered_building_type_is_accepted(kind):
    assert request(property_type=kind).property_type == kind


@pytest.mark.parametrize("kind", ["house", "Condo", "ALL", "townhouse", "", None, 1])
def test_other_building_types_are_rejected(kind):
    with pytest.raises(ValueError):
        request(property_type=kind)


# --- the radius is the radius the engine filters by --------------------------------------------------------------

DISTANCES = [0.4, 0.9, 1.4, 2.5, 4.9]


@pytest.mark.parametrize("radius, expected", [(0.5, 1), (1.0, 2), (2.0, 3), (3.0, 4), (5.0, 5)])
def test_each_radius_keeps_only_listings_inside_it(radius, expected):
    result = run_valuation(request(search_radius_miles=radius), ListSource([at(d) for d in DISTANCES]), min_comparables=1)
    assert result["funnel"]["comparables_after_distance_filter"] == expected
    assert result["search"]["radius_miles"] == radius and result["search"]["distance_units"] == "miles"


def test_the_default_radius_is_one_mile():
    result = run_valuation(ValuationRequest(**{k: v for k, v in BODY.items()}), ListSource([at(d) for d in DISTANCES]), min_comparables=1)
    assert result["funnel"]["comparables_after_distance_filter"] == 2 and result["search"]["radius_miles"] == 1.0


def test_a_larger_radius_can_turn_no_data_into_a_valuation():
    source = ListSource([at(1.4, 2000), at(1.6, 2100), at(1.8, 2200)])
    with pytest.raises(InsufficientDataError):
        run_valuation(request(search_radius_miles=1.0), source)
    assert run_valuation(request(search_radius_miles=2.0), source)["comparable_count"] == 3


def test_the_source_is_told_the_same_radius_the_engine_uses():
    source = ListSource([at(0.4)])
    run_valuation(request(search_radius_miles=3.0, lookback_days=180, property_type="condo"), source, min_comparables=1)
    subject = source.subjects[0]
    assert (subject.search_radius_miles, subject.lookback_days, subject.property_type) == (3.0, 180, "condo")


def test_the_source_is_told_when_square_feet_are_missing():
    source = ListSource([at(0.4)])
    run_valuation(request(sqft=None), source, min_comparables=1)
    assert source.subjects[0].sqft is None


def test_the_mock_data_follows_the_radius_too():
    counts = [run_valuation(request(search_radius_miles=r), MockComparableSource(), min_comparables=1)["funnel"]["comparables_after_distance_filter"]
              for r in RADIUS_OPTIONS_MILES]
    assert counts == sorted(counts) and counts[1] == 24  # 1 mile keeps the 24 the earlier phases showed


# --- nearest listing --------------------------------------------------------------------------------------------

def test_the_nearest_listing_is_reported_even_when_it_is_outside_the_radius():
    with pytest.raises(InsufficientDataError) as info:
        run_valuation(request(search_radius_miles=1.0), ListSource([at(1.4), at(2.0), at(3.0)]))
    assert info.value.search["nearest_listing_miles"] == 1.4
    assert info.value.to_response()["search"]["nearest_listing_miles"] == 1.4


def test_the_nearest_listing_is_the_closest_of_all_fetched():
    result = run_valuation(request(), ListSource([at(0.9), at(0.2), at(0.5), at(4.0)]), min_comparables=1)
    assert result["search"]["nearest_listing_miles"] == 0.2


def test_the_nearest_listing_is_none_when_nothing_was_fetched():
    with pytest.raises(InsufficientDataError) as info:
        run_valuation(request(), ListSource([]))
    assert info.value.search["nearest_listing_miles"] is None


# --- square feet are optional ------------------------------------------------------------------------------------

def test_without_square_feet_the_size_filter_is_skipped():
    source = ListSource([at(0.3, sqft=1400), at(0.4, sqft=3000), at(0.5, sqft=600), at(0.6, sqft=1450)])
    with_sqft = run_valuation(request(sqft=1400), source, min_comparables=1)
    without = run_valuation(request(sqft=None), source, min_comparables=1)
    assert with_sqft["comparable_count"] == 2
    assert without["comparable_count"] == 4  # every size qualifies
    assert without["search"]["sqft_used"] is False and with_sqft["search"]["sqft_used"] is True


def test_a_valuation_runs_without_square_feet():
    result = run_valuation(request(sqft=None), ListSource([at(0.1 * i, 2000 + 10 * i) for i in range(1, 6)]))
    assert result["recommended_rent"] > 0 and result["comparable_count"] == 5


@pytest.mark.parametrize(
    "count, with_sqft, without_sqft",
    [(3, "low", "low"), (4, "low", "low"), (5, "medium", "low"), (9, "medium", "low"), (10, "high", "medium"), (15, "high", "medium")],
)
def test_missing_square_feet_lower_confidence_one_level(count, with_sqft, without_sqft):
    source = ListSource([at(0.01 * i, 2000 + i) for i in range(1, count + 1)])
    assert run_valuation(request(sqft=1400), source)["confidence"] == with_sqft
    assert run_valuation(request(sqft=None), source)["confidence"] == without_sqft


def test_the_result_explains_the_lowered_confidence():
    source = ListSource([at(0.01 * i, 2000 + i) for i in range(1, 11)])
    notes = run_valuation(request(sqft=None), source)["confidence_notes"]
    assert notes == ["Square feet were not given, so listings were not matched on size and confidence was lowered one level."]
    assert run_valuation(request(sqft=1400), source)["confidence_notes"] == []


@pytest.mark.parametrize("level, lower", [("high", "medium"), ("medium", "low"), ("low", "low")])
def test_lower_confidence(level, lower):
    assert quality.lower_confidence(level) == lower


def test_square_feet_missing_is_noted_in_an_insufficient_result_too():
    with pytest.raises(InsufficientDataError) as info:
        run_valuation(request(sqft=None), ListSource([at(0.1)]))
    assert info.value.search["sqft_used"] is False


def test_the_valuation_is_saved_without_square_feet(repository):
    run_valuation(request(sqft=None), ListSource([at(0.1 * i) for i in range(1, 5)]), repository)
    saved = repository.get_recent_valuations(1)[0]
    assert saved["sqft"] is None and saved["address"] == "123 Main St"


# --- lookback window -----------------------------------------------------------------------------------------------

AGES = [10, 30, 31, 60, 90, 91, 150, 200, 365, 400, None]


def aged():
    return [at(0.01 * (i + 1), 2000 + i, days_on_market=age) for i, age in enumerate(AGES)]


@pytest.mark.parametrize(
    "days, kept",
    [(30, 3), (90, 6), (180, 8), (365, 10)],  # ages <= days, plus the one with no known age
)
def test_the_lookback_keeps_listings_within_it_and_those_of_unknown_age(days, kept):
    result = run_valuation(request(lookback_days=days), ListSource(aged()), min_comparables=1)
    assert result["funnel"]["comparables_after_lookback_filter"] == kept


def test_a_listing_exactly_as_old_as_the_window_is_kept():
    result = run_valuation(request(lookback_days=90), ListSource([at(0.1, days_on_market=90), at(0.2, days_on_market=91)]), min_comparables=1)
    assert result["comparable_count"] == 1


def test_the_lookback_is_a_funnel_stage_before_distance():
    far_and_old = at(3.0, days_on_market=400)  # fails both: counted at the first stage it fails
    result = run_valuation(request(), ListSource([at(0.1, days_on_market=5), at(0.2, days_on_market=400), far_and_old]), min_comparables=1)
    assert result["funnel"]["comparables_fetched"] == 3
    assert result["funnel"]["comparables_after_lookback_filter"] == 1
    assert result["funnel"]["comparables_after_distance_filter"] == 1


def test_data_without_ages_is_unaffected_by_the_lookback():
    results = [run_valuation(request(lookback_days=d), MockComparableSource())["funnel"]["comparables_after_lookback_filter"] for d in LOOKBACK_OPTIONS_DAYS]
    assert results == [26] * 4


def test_filter_by_lookback_unit():
    items = [{"days_on_market": 5}, {"days_on_market": 100}, {"days_on_market": None}, {}]
    assert comps.filter_by_lookback(items, 30) == [{"days_on_market": 5}, {"days_on_market": None}, {}]


def test_nearest_miles_unit():
    assert comps.nearest_miles([], LAT, LON) is None
    assert comps.nearest_miles([at(2.0), at(0.5), at(1.0)], LAT, LON) == pytest.approx(0.5, abs=0.01)


# --- the API -------------------------------------------------------------------------------------------------------------

def post(body, source=None):
    if source is not None:
        app.dependency_overrides[get_comparable_source] = lambda: source
    try:
        return client.post("/valuation", json=body)
    finally:
        app.dependency_overrides.pop(get_comparable_source, None)


def test_the_api_works_without_any_of_the_new_fields():
    body = {k: v for k, v in BODY.items()}
    response = post(body, ListSource([at(0.1 * i) for i in range(1, 5)]))
    assert response.status_code == 200
    assert response.json()["search"] == {
        "radius_miles": 1.0, "distance_units": "miles", "lookback_days": 90, "property_type": "all",
        "sqft_used": True, "minimum_comparables": 3, "nearest_listing_miles": 0.1,
    }
    assert response.json()["confidence_notes"] == []


def test_the_api_accepts_the_new_fields():
    source = ListSource([at(1.5, 2000 + i) for i in range(4)])
    response = post({**BODY, "search_radius_miles": 2, "lookback_days": 180, "property_type": "condo"}, source)
    assert response.status_code == 200
    assert (source.subjects[0].search_radius_miles, source.subjects[0].lookback_days, source.subjects[0].property_type) == (2.0, 180, "condo")
    assert response.json()["search"]["radius_miles"] == 2.0


def test_the_api_runs_without_square_feet():
    body = {k: v for k, v in BODY.items() if k != "sqft"}
    response = post(body, ListSource([at(0.05 * i, 2000 + i) for i in range(1, 11)]))  # 10 listings, all inside 1 mile
    assert response.status_code == 200
    assert response.json()["confidence"] == "medium" and len(response.json()["confidence_notes"]) == 1  # high, lowered one level
    assert response.json()["search"]["sqft_used"] is False


def test_the_api_accepts_an_explicit_null_for_square_feet():
    assert post({**BODY, "sqft": None}, ListSource([at(0.1 * i) for i in range(1, 5)])).status_code == 200


@pytest.mark.parametrize(
    "extra",
    [{"search_radius_miles": 4}, {"search_radius_miles": 0}, {"search_radius_miles": "far"}, {"search_radius_miles": True},
     {"lookback_days": 45}, {"lookback_days": 0}, {"property_type": "house"}, {"property_type": "Condo"}],
)
def test_the_api_rejects_unoffered_choices(extra):
    response = post({**BODY, **extra}, ListSource([]))
    assert response.status_code == 422
    field = next(iter(extra))
    assert field in response.json()["detail"][0]["loc"]  # the message points at the field that was refused


def test_an_api_validation_message_lists_the_choices():
    text = post({**BODY, "search_radius_miles": 4}, ListSource([])).text
    assert "0.5, 1, 2, 3, 5" in text and "miles" in text


def test_the_insufficient_data_body_carries_the_search_and_nearest_listing():
    response = post({**BODY, "search_radius_miles": 0.5}, ListSource([at(1.2), at(2.2)]))
    assert response.status_code == 404
    body = response.json()
    assert body["search"]["radius_miles"] == 0.5 and body["search"]["nearest_listing_miles"] == 1.2
    assert body["funnel"]["comparables_fetched"] == 2 and body["funnel"]["comparables_after_distance_filter"] == 0


def test_history_shows_a_missing_square_footage_as_null(repository):
    body = {k: v for k, v in BODY.items() if k != "sqft"}
    post(body, ListSource([at(0.1 * i) for i in range(1, 5)]))
    items = client.get("/history").json()
    assert items[0]["sqft"] is None
