from app.services.comparables import (
    MOCK_COMPARABLES,
    filter_by_bathrooms,
    filter_by_bedrooms,
    filter_by_distance,
    filter_by_sqft,
    load_comparables,
)


def make(**overrides):
    base = {"address": "x", "rent": 2500, "distance_miles": 0.5, "beds": 3, "baths": 2, "sqft": 1400}
    return {**base, **overrides}


def test_dataset_size_and_outliers_present():
    rents = [c["rent"] for c in MOCK_COMPARABLES]
    assert 20 <= len(MOCK_COMPARABLES) <= 30
    assert 900 in rents and 8000 in rents


def test_load_comparables_returns_independent_copy():
    first = load_comparables()
    first[0]["rent"] = -1
    assert load_comparables()[0]["rent"] != -1


def test_filter_by_distance():
    comps = [make(distance_miles=0.5), make(distance_miles=1.0), make(distance_miles=1.1)]
    assert [c["distance_miles"] for c in filter_by_distance(comps, max_miles=1.0)] == [0.5, 1.0]


def test_filter_by_bedrooms_allows_plus_minus_one():
    comps = [make(beds=n) for n in range(1, 6)]
    kept = [c["beds"] for c in filter_by_bedrooms(comps, beds=3)]
    assert kept == [2, 3, 4]


def test_filter_by_bedrooms_custom_tolerance():
    comps = [make(beds=n) for n in range(1, 6)]
    assert [c["beds"] for c in filter_by_bedrooms(comps, beds=3, tolerance=0)] == [3]


def test_filter_by_bathrooms_allows_plus_minus_one():
    comps = [make(baths=b) for b in (0.5, 1, 1.5, 2, 3, 3.5)]
    assert [c["baths"] for c in filter_by_bathrooms(comps, baths=2)] == [1, 1.5, 2, 3]


def test_filter_by_sqft_is_inclusive_at_plus_minus_20_percent():
    # Subject 1400 sqft -> allowed range is 1120..1680 inclusive.
    comps = [make(sqft=s) for s in (1119, 1120, 1400, 1680, 1681)]
    assert [c["sqft"] for c in filter_by_sqft(comps, sqft=1400)] == [1120, 1400, 1680]


def test_filter_by_sqft_custom_tolerance():
    comps = [make(sqft=s) for s in (1250, 1400, 1550)]
    assert [c["sqft"] for c in filter_by_sqft(comps, sqft=1400, tolerance=0.05)] == [1400]
