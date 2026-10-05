import logging

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SUBJECT_LATITUDE as LAT
from app.config import DEFAULT_SUBJECT_LONGITUDE as LON
from app.main import app
from app.routers.valuation import get_comparable_source
from app.schemas import ValuationRequest
from app.services.data_sources import ComparableDataSource, MockComparableSource
from app.services.quality import Funnel, confidence_for
from app.services.valuation_engine import InsufficientDataError, run_valuation

SUBJECT = ValuationRequest(address="123 Main St", beds=3, baths=2, sqft=1400)
BODY = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400, "latitude": LAT, "longitude": LON}
client = TestClient(app)


def comp(rent, **overrides):
    base = {
        "address": f"{rent} Test St", "rent": rent, "latitude": LAT, "longitude": LON,
        "beds": 3, "baths": 2, "sqft": 1400,
    }
    base.update(overrides)
    return base


class ListSource(ComparableDataSource):
    """Returns exactly the comparables it is given. No network, no provider."""

    def __init__(self, comparables):
        self.comparables = comparables

    def get_comparables(self, subject=None):
        return [dict(c) for c in self.comparables]


def matching(n):
    """n comparables that pass every filter, with rents close together (no outliers)."""
    return [comp(2000 + 10 * i) for i in range(n)]


def post_with(comparables):
    app.dependency_overrides[get_comparable_source] = lambda: ListSource(comparables)
    try:
        return client.post("/valuation", json=BODY)
    finally:
        app.dependency_overrides.pop(get_comparable_source, None)


# --- confidence scoring ----------------------------------------------------------------

@pytest.mark.parametrize(
    "used, expected",
    [(0, "low"), (1, "low"), (2, "low"), (3, "low"), (4, "low"),
     (5, "medium"), (7, "medium"), (9, "medium"),
     (10, "high"), (11, "high"), (16, "high"), (500, "high")],
)
def test_confidence_thresholds(used, expected):
    assert confidence_for(used) == expected


@pytest.mark.parametrize("n", [3, 4])
def test_low_confidence(n):
    assert run_valuation(SUBJECT, ListSource(matching(n)))["confidence"] == "low"


@pytest.mark.parametrize("n", [5, 9])
def test_medium_confidence(n):
    assert run_valuation(SUBJECT, ListSource(matching(n)))["confidence"] == "medium"


@pytest.mark.parametrize("n", [10, 15])
def test_high_confidence(n):
    assert run_valuation(SUBJECT, ListSource(matching(n)))["confidence"] == "high"


def test_confidence_counts_comparables_used_not_fetched():
    # 12 fetched, but 3 are far away, so only 9 are used: medium, not high.
    far = [comp(2500 + i, latitude=40.7, longitude=-74.0) for i in range(3)]
    assert run_valuation(SUBJECT, ListSource(matching(9) + far))["confidence"] == "medium"


def test_confidence_counts_after_outlier_removal():
    # 10 candidates, one extreme outlier dropped: 9 used, so medium.
    result = run_valuation(SUBJECT, ListSource(matching(9) + [comp(50000)]))
    assert result["funnel"]["comparables_used"] == 9
    assert result["confidence"] == "medium"


def test_a_lowered_minimum_still_reports_low_for_one_or_two():
    assert run_valuation(SUBJECT, ListSource(matching(1)), min_comparables=1)["confidence"] == "low"


@pytest.mark.parametrize("n, expected", [(3, "low"), (5, "medium"), (10, "high")])
def test_api_reports_confidence(n, expected):
    response = post_with(matching(n))
    assert response.status_code == 200
    assert response.json()["confidence"] == expected


# --- funnel counts ---------------------------------------------------------------------

def mixed_comparables():
    far = [comp(2600 + i, latitude=40.7, longitude=-74.0) for i in range(3)]  # fail distance
    wrong_beds = [comp(1500 + i, beds=6) for i in range(2)]  # near, fail the attribute filter
    too_big = [comp(3900, sqft=3000)]  # near, fail the attribute filter
    far_and_wrong = [comp(1000, latitude=40.7, longitude=-74.0, beds=9)]  # counted once, at distance
    good = [comp(r) for r in (2000, 2100, 2200, 2300, 2400)]
    outlier = [comp(20000)]  # passes every filter, removed as an outlier
    return far + wrong_beds + too_big + far_and_wrong + good + outlier


def test_funnel_counts_each_stage():
    result = run_valuation(SUBJECT, ListSource(mixed_comparables()))
    assert result["funnel"] == {
        "comparables_fetched": 13,
        "comparables_after_distance_filter": 9,  # 13 - 3 far - 1 far-and-wrong
        "comparables_after_attribute_filter": 6,  # 9 - 2 wrong beds - 1 too big
        "comparables_after_outlier_filter": 5,  # 6 - 1 outlier
        "comparables_used": 5,
        "comparables_after_lookback_filter": 13,  # the test comparables have no listing age, so all pass
    }
    assert result["comparable_count"] == 5
    assert result["confidence"] == "medium"


def test_funnel_used_matches_comparable_count():
    result = run_valuation(SUBJECT, ListSource(mixed_comparables()))
    assert result["funnel"]["comparables_used"] == result["comparable_count"]


def test_funnel_with_nothing_removed():
    funnel = run_valuation(SUBJECT, ListSource(matching(4)))["funnel"]
    assert set(funnel.values()) == {4}


def test_funnel_stages_never_grow_on_the_mock_data():
    f = run_valuation(SUBJECT, MockComparableSource())["funnel"]
    assert (f["comparables_fetched"], f["comparables_after_distance_filter"]) == (26, 24)
    assert (f["comparables_after_attribute_filter"], f["comparables_after_outlier_filter"]) == (18, 16)
    stages = ["comparables_fetched", "comparables_after_lookback_filter", "comparables_after_distance_filter",
              "comparables_after_attribute_filter", "comparables_after_outlier_filter", "comparables_used"]
    counts = [f[name] for name in stages]
    assert counts == sorted(counts, reverse=True) and counts[1] == 26


def test_funnel_as_dict_has_the_documented_keys():
    assert list(Funnel(5, 4, 3, 2, 2, 5).as_dict()) == [
        "comparables_fetched", "comparables_after_distance_filter", "comparables_after_attribute_filter",
        "comparables_after_outlier_filter", "comparables_used", "comparables_after_lookback_filter",
    ]


def test_api_response_includes_the_funnel():
    body = post_with(mixed_comparables()).json()
    assert body["funnel"]["comparables_fetched"] == 13
    assert body["funnel"]["comparables_used"] == body["comparable_count"] == 5
    assert body["confidence"] == "medium"


# --- diagnostics when there is not enough data ----------------------------------------

def test_insufficient_data_carries_the_funnel():
    with pytest.raises(InsufficientDataError) as info:
        run_valuation(SUBJECT, ListSource(mixed_comparables()), min_comparables=6)
    assert info.value.funnel.comparables_fetched == 13
    assert info.value.funnel.comparables_used == 5


def test_insufficient_data_response_includes_the_funnel_and_no_confidence():
    response = post_with([comp(2000), comp(2100)] + [comp(3000, latitude=40.7, longitude=-74.0)] * 4)
    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert body["funnel"] == {
        "comparables_fetched": 6, "comparables_after_distance_filter": 2,
        "comparables_after_attribute_filter": 2, "comparables_after_outlier_filter": 2,
        "comparables_used": 2, "comparables_after_lookback_filter": 6,
    }
    assert body["search"]["radius_miles"] == 1.0 and body["search"]["nearest_listing_miles"] == 0.0
    assert "confidence" not in body


def test_zero_comparables_response_shows_what_was_fetched():
    body = post_with([comp(2000, latitude=40.7, longitude=-74.0)] * 5).json()
    assert body["comparable_count"] == 0
    assert body["funnel"]["comparables_fetched"] == 5
    assert body["funnel"]["comparables_after_distance_filter"] == 0


def test_an_empty_source_gives_a_zero_funnel():
    body = post_with([]).json()
    assert set(body["funnel"].values()) == {0}


# --- structured logging ----------------------------------------------------------------

def funnel_records(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("valuation_funnel")]


def test_each_valuation_logs_one_funnel_line(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.valuation_engine"):
        run_valuation(SUBJECT, ListSource(mixed_comparables()))
    (record,) = funnel_records(caplog)
    assert record.getMessage() == (
        "valuation_funnel status=ok fetched=13 after_lookback=13 after_distance=9 after_attributes=6 "
        "after_outliers=5 used=5 minimum=3 confidence=medium building_type=all radius_miles=1 "
        "lookback_days=90 nearest_miles=0.00 sqft=given"
    )


def test_the_log_record_carries_structured_fields(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.valuation_engine"):
        run_valuation(SUBJECT, ListSource(mixed_comparables()))
    (record,) = funnel_records(caplog)
    assert record.valuation_status == "ok"
    assert record.confidence == "medium"
    assert record.minimum_required == 3
    assert record.funnel["comparables_fetched"] == 13
    assert record.funnel["comparables_used"] == 5


def test_insufficient_data_is_logged_too(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.valuation_engine"):
        with pytest.raises(InsufficientDataError):
            run_valuation(SUBJECT, ListSource(matching(2)))
    (record,) = funnel_records(caplog)
    assert "status=insufficient_data" in record.getMessage()
    assert "confidence=none" in record.getMessage()
    assert record.confidence is None
    assert record.funnel["comparables_used"] == 2


def test_the_funnel_log_does_not_include_the_address(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.valuation_engine"):
        run_valuation(ValuationRequest(address="742 Evergreen Terrace", beds=3, baths=2, sqft=1400), ListSource(matching(4)))
    assert "Evergreen" not in caplog.text


def test_geocoding_errors_log_no_funnel(caplog):
    # An unknown address fails before any comparables are fetched, so there is no funnel.
    with caplog.at_level(logging.INFO, logger="app.services.valuation_engine"):
        client.post("/valuation", json={"address": "1 Nowhere Rd, Atlantis, CA", "beds": 3, "baths": 2, "sqft": 1400})
    assert funnel_records(caplog) == []
