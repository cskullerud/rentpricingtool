"""Filters that narrow a list of comparable properties to those similar to a subject."""
from app.services.data_sources.base import Comparable
from app.services.geo import miles_between_points, nearest_distance_miles

DEFAULT_MAX_DISTANCE_MILES = 1.0
DEFAULT_BEDROOM_TOLERANCE = 1
DEFAULT_BATHROOM_TOLERANCE = 1
DEFAULT_SQFT_TOLERANCE = 0.20  # +/- 20%


def filter_by_distance(
    comparables: list[Comparable],
    latitude: float,
    longitude: float,
    max_miles: float = DEFAULT_MAX_DISTANCE_MILES,
) -> list[Comparable]:
    """Keep comparables within `max_miles` (inclusive) of the given point.

    Distance is the great-circle distance, calculated from each comparable's coordinates.
    """
    return [
        c
        for c in comparables
        if miles_between_points(latitude, longitude, c["latitude"], c["longitude"]) <= max_miles
    ]


def filter_by_bedrooms(
    comparables: list[Comparable], beds: int, tolerance: int = DEFAULT_BEDROOM_TOLERANCE
) -> list[Comparable]:
    """Keep comparables within +/- `tolerance` bedrooms of the subject."""
    return [c for c in comparables if abs(c["beds"] - beds) <= tolerance]


def filter_by_bathrooms(
    comparables: list[Comparable], baths: float, tolerance: float = DEFAULT_BATHROOM_TOLERANCE
) -> list[Comparable]:
    """Keep comparables within +/- `tolerance` bathrooms of the subject."""
    return [c for c in comparables if abs(c["baths"] - baths) <= tolerance]


def filter_by_sqft(
    comparables: list[Comparable], sqft: int, tolerance: float = DEFAULT_SQFT_TOLERANCE
) -> list[Comparable]:
    """Keep comparables within +/- `tolerance` (a fraction, 0.20 = 20%) of the subject's sqft."""
    allowed = sqft * tolerance + 1e-9  # epsilon guards float error at the exact boundary
    return [c for c in comparables if abs(c["sqft"] - sqft) <= allowed]


def filter_by_lookback(comparables: list[Comparable], days: int) -> list[Comparable]:
    """Keep comparables listed within the last `days` days.

    A comparable whose age is unknown (no `days_on_market`, as with the sample data) is kept:
    it cannot be shown to be too old.
    """
    return [c for c in comparables if c.get("days_on_market") is None or c["days_on_market"] <= days]


def nearest_miles(comparables: list[Comparable], latitude: float, longitude: float) -> float | None:
    """Distance in miles from the point to the closest comparable, or None if there are none."""
    return nearest_distance_miles(latitude, longitude, comparables)
