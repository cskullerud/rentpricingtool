from app.config import DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE
from app.services.comparables import (
    filter_by_bathrooms,
    filter_by_bedrooms,
    filter_by_distance,
    filter_by_sqft,
)
from app.services.data_sources.mock_source import MOCK_COMPARABLES, MockComparableSource
from app.services.geo import miles_between_points

LAT, LON = DEFAULT_SUBJECT_LATITUDE, DEFAULT_SUBJECT_LONGITUDE
MILES_PER_DEGREE_LAT = 69.0934  # one degree of latitude, in miles


def make(**overrides):
    base = {"address": "x", "rent": 2500, "latitude": LAT, "longitude": LON, "beds": 3, "baths": 2, "sqft": 1400}
    return {**base, **overrides}


def test_dataset_size_and_outliers_present():
    rents = [c["rent"] for c in MOCK_COMPARABLES]
    assert 20 <= len(MOCK_COMPARABLES) <= 30
    assert 900 in rents and 8000 in rents


def test_get_comparables_returns_independent_copy():
    source = MockComparableSource()
    first = source.get_comparables()
    first[0]["rent"] = -1
    assert source.get_comparables()[0]["rent"] != -1


def north_of_subject(miles):
    """A comparable `miles` due north of the subject (distance along a meridian is exact)."""
    return make(latitude=LAT + miles / MILES_PER_DEGREE_LAT)


def test_filter_by_distance_is_calculated_from_coordinates():
    comps = [north_of_subject(m) for m in (0.0, 0.5, 0.99, 1.01, 3.0)]
    kept = filter_by_distance(comps, LAT, LON, max_miles=1.0)
    distances = [miles_between_points(LAT, LON, c["latitude"], c["longitude"]) for c in kept]
    assert len(kept) == 3
    assert all(d <= 1.0 for d in distances)


def test_filter_by_distance_moves_with_the_subject():
    comp = north_of_subject(0.5)
    assert filter_by_distance([comp], LAT, LON, max_miles=1.0) == [comp]
    far_subject_lat = LAT + 5 / MILES_PER_DEGREE_LAT  # subject now ~4.5 miles from the comp
    assert filter_by_distance([comp], far_subject_lat, LON, max_miles=1.0) == []


def test_filter_by_distance_is_inclusive_at_the_limit():
    comp = north_of_subject(0.8)
    exact = miles_between_points(LAT, LON, comp["latitude"], comp["longitude"])
    assert filter_by_distance([comp], LAT, LON, max_miles=exact) == [comp]
    assert filter_by_distance([comp], LAT, LON, max_miles=exact - 0.001) == []


def test_filter_by_distance_default_radius_is_one_mile():
    comps = [north_of_subject(0.9), north_of_subject(1.1)]
    assert len(filter_by_distance(comps, LAT, LON)) == 1


def test_dataset_is_clustered_around_la_mesa_san_diego():
    for c in MOCK_COMPARABLES:
        assert 32.6 <= c["latitude"] <= 32.9, c["address"]
        assert -117.2 <= c["longitude"] <= -116.9, c["address"]


def test_dataset_has_24_close_and_2_far_comparables():
    distances = [miles_between_points(LAT, LON, c["latitude"], c["longitude"]) for c in MOCK_COMPARABLES]
    assert sum(d <= 1.0 for d in distances) == 24
    assert sorted(round(d, 1) for d in distances)[-2:] == [2.5, 3.2]


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
