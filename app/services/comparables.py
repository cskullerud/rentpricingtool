"""Mock comparable-property data and the filters that narrow it to a subject property."""
from typing import TypedDict


class Comparable(TypedDict):
    address: str
    rent: int
    distance_miles: float
    beds: int
    baths: float
    sqft: int


DEFAULT_MAX_DISTANCE_MILES = 1.0
DEFAULT_BEDROOM_TOLERANCE = 1
DEFAULT_BATHROOM_TOLERANCE = 1
DEFAULT_SQFT_TOLERANCE = 0.20  # +/- 20%

# distance_miles is measured from the subject property. The set deliberately includes
# rent outliers (900 and 8000) and properties that the filters should exclude.
MOCK_COMPARABLES: list[Comparable] = [
    # Close, similar properties
    {"address": "101 Oak Ave", "rent": 2450, "distance_miles": 0.3, "beds": 3, "baths": 2, "sqft": 1380},
    {"address": "118 Oak Ave", "rent": 2500, "distance_miles": 0.4, "beds": 3, "baths": 2, "sqft": 1420},
    {"address": "205 Maple St", "rent": 2550, "distance_miles": 0.5, "beds": 3, "baths": 2, "sqft": 1450},
    {"address": "212 Maple St", "rent": 2400, "distance_miles": 0.6, "beds": 3, "baths": 2, "sqft": 1350},
    {"address": "330 Cedar Ln", "rent": 2600, "distance_miles": 0.7, "beds": 3, "baths": 2, "sqft": 1500},
    {"address": "345 Cedar Ln", "rent": 2350, "distance_miles": 0.8, "beds": 3, "baths": 2, "sqft": 1300},
    {"address": "410 Birch Rd", "rent": 2700, "distance_miles": 0.9, "beds": 3, "baths": 2.5, "sqft": 1550},
    {"address": "422 Birch Rd", "rent": 2300, "distance_miles": 0.5, "beds": 2, "baths": 2, "sqft": 1200},
    {"address": "515 Elm St", "rent": 2650, "distance_miles": 0.4, "beds": 4, "baths": 2, "sqft": 1600},
    {"address": "528 Elm St", "rent": 2475, "distance_miles": 0.6, "beds": 3, "baths": 1.5, "sqft": 1400},
    {"address": "601 Pine Ct", "rent": 2525, "distance_miles": 0.2, "beds": 3, "baths": 2, "sqft": 1410},
    {"address": "617 Pine Ct", "rent": 2575, "distance_miles": 0.3, "beds": 3, "baths": 2, "sqft": 1440},
    {"address": "702 Walnut Dr", "rent": 2425, "distance_miles": 0.7, "beds": 3, "baths": 2, "sqft": 1330},
    {"address": "715 Walnut Dr", "rent": 2750, "distance_miles": 0.9, "beds": 4, "baths": 3, "sqft": 1650},
    {"address": "803 Spruce Way", "rent": 2200, "distance_miles": 0.8, "beds": 2, "baths": 1, "sqft": 1150},
    {"address": "819 Spruce Way", "rent": 2625, "distance_miles": 0.5, "beds": 3, "baths": 2, "sqft": 1520},
    # Intentional rent outliers (otherwise a good match)
    {"address": "55 Sublet Ct", "rent": 900, "distance_miles": 0.4, "beds": 3, "baths": 2, "sqft": 1400},
    {"address": "9 Penthouse Pl", "rent": 8000, "distance_miles": 0.6, "beds": 3, "baths": 2, "sqft": 1450},
    # Should be removed by the filters
    {"address": "1200 Far Ave", "rent": 2550, "distance_miles": 2.5, "beds": 3, "baths": 2, "sqft": 1400},
    {"address": "1315 Studio St", "rent": 1500, "distance_miles": 0.3, "beds": 1, "baths": 1, "sqft": 600},
    {"address": "1420 Mansion Blvd", "rent": 4200, "distance_miles": 0.9, "beds": 6, "baths": 4, "sqft": 3200},
    {"address": "1525 Tiny Ln", "rent": 1900, "distance_miles": 0.4, "beds": 3, "baths": 2, "sqft": 900},
    {"address": "1630 Estate Dr", "rent": 3400, "distance_miles": 0.8, "beds": 3, "baths": 2, "sqft": 2100},
    {"address": "1744 Bath Rd", "rent": 2300, "distance_miles": 0.6, "beds": 3, "baths": 4, "sqft": 1400},
    {"address": "1850 Remote Rd", "rent": 2450, "distance_miles": 3.2, "beds": 4, "baths": 2, "sqft": 1500},
    {"address": "1962 Loft Ave", "rent": 1800, "distance_miles": 0.5, "beds": 1, "baths": 1, "sqft": 700},
]


def load_comparables() -> list[Comparable]:
    """Return a fresh copy of the mock dataset so callers can't modify it."""
    return [dict(c) for c in MOCK_COMPARABLES]  # type: ignore[misc]


def filter_by_distance(
    comparables: list[Comparable], max_miles: float = DEFAULT_MAX_DISTANCE_MILES
) -> list[Comparable]:
    return [c for c in comparables if c["distance_miles"] <= max_miles]


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
