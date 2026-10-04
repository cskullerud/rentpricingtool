import pytest

from app.services.geo import haversine_distance, miles_between_points

SAN_DIEGO = (32.7157, -117.1611)
LOS_ANGELES = (34.0522, -118.2437)
NEW_YORK = (40.7128, -74.0060)
LONDON = (51.5074, -0.1278)


def test_zero_distance_for_the_same_point():
    assert haversine_distance(32.7678, -117.0231, 32.7678, -117.0231) == 0.0
    assert miles_between_points(32.7678, -117.0231, 32.7678, -117.0231) == 0.0


def test_zero_distance_at_the_poles_and_across_the_antimeridian():
    assert haversine_distance(90, 0, 90, 123) == pytest.approx(0, abs=1e-9)
    assert haversine_distance(0, 180, 0, -180) == pytest.approx(0, abs=1e-9)


def test_known_distance_one_degree_of_latitude():
    # One degree along a meridian is 111.195 km on the mean-radius sphere.
    assert haversine_distance(0, 0, 1, 0) == pytest.approx(111.195, abs=0.01)


def test_known_distance_new_york_to_london():
    assert haversine_distance(*NEW_YORK, *LONDON) == pytest.approx(5570, abs=15)
    assert miles_between_points(*NEW_YORK, *LONDON) == pytest.approx(3461, abs=10)


def test_known_distance_san_diego_to_los_angeles():
    assert miles_between_points(*SAN_DIEGO, *LOS_ANGELES) == pytest.approx(112, abs=1.5)


def test_distance_is_symmetric():
    forward = haversine_distance(*SAN_DIEGO, *LOS_ANGELES)
    backward = haversine_distance(*LOS_ANGELES, *SAN_DIEGO)
    assert forward == pytest.approx(backward)


def test_miles_are_kilometers_converted():
    km = haversine_distance(*SAN_DIEGO, *LOS_ANGELES)
    assert miles_between_points(*SAN_DIEGO, *LOS_ANGELES) == pytest.approx(km / 1.609344)


def test_antipodal_points_do_not_error():
    # Half of Earth's circumference: pi * R = 20,015 km.
    assert haversine_distance(0, 0, 0, 180) == pytest.approx(20015.1, abs=1)
