from app.services.data_sources.base import Comparable, ComparableDataSource, SubjectProperty

# Coordinates are decimal degrees clustered around La Mesa / San Diego, CA. Most points are
# within a mile of the default subject (config.DEFAULT_SUBJECT_LATITUDE/LONGITUDE); two are
# deliberately several miles away. The set also includes rent outliers (900 and 8000) and
# properties that the filters should exclude. Street names are fictional.
MOCK_COMPARABLES: list[Comparable] = [
    # Close, similar properties
    {"address": "101 Oak Ave", "rent": 2450, "latitude": 32.77214, "longitude": -117.02310, "beds": 3, "baths": 2, "sqft": 1380},
    {"address": "118 Oak Ave", "rent": 2500, "latitude": 32.76353, "longitude": -117.01845, "beds": 3, "baths": 2, "sqft": 1420},
    {"address": "205 Maple St", "rent": 2550, "latitude": 32.76843, "longitude": -117.03167, "beds": 3, "baths": 2, "sqft": 1450},
    {"address": "212 Maple St", "rent": 2400, "latitude": 32.77308, "longitude": -117.01490, "beds": 3, "baths": 2, "sqft": 1350},
    {"address": "330 Cedar Ln", "rent": 2600, "latitude": 32.75782, "longitude": -117.02520, "beds": 3, "baths": 2, "sqft": 1500},
    {"address": "345 Cedar Ln", "rent": 2350, "latitude": 32.77757, "longitude": -117.03049, "beds": 3, "baths": 2, "sqft": 1300},
    {"address": "410 Birch Rd", "rent": 2700, "latitude": 32.76442, "longitude": -117.00814, "beds": 3, "baths": 2.5, "sqft": 1550},
    {"address": "422 Birch Rd", "rent": 2300, "latitude": 32.76446, "longitude": -117.03074, "beds": 2, "baths": 2, "sqft": 1200},
    {"address": "515 Elm St", "rent": 2650, "latitude": 32.77324, "longitude": -117.02074, "beds": 4, "baths": 2, "sqft": 1600},
    {"address": "528 Elm St", "rent": 2475, "latitude": 32.75977, "longitude": -117.01916, "beds": 3, "baths": 1.5, "sqft": 1400},
    {"address": "601 Pine Ct", "rent": 2525, "latitude": 32.76903, "longitude": -117.02622, "beds": 3, "baths": 2, "sqft": 1410},
    {"address": "617 Pine Ct", "rent": 2575, "latitude": 32.76910, "longitude": -117.01817, "beds": 3, "baths": 2, "sqft": 1440},
    {"address": "702 Walnut Dr", "rent": 2425, "latitude": 32.75903, "longitude": -117.02914, "beds": 3, "baths": 2, "sqft": 1330},
    {"address": "715 Walnut Dr", "rent": 2750, "latitude": 32.78052, "longitude": -117.02643, "beds": 4, "baths": 3, "sqft": 1650},
    {"address": "803 Spruce Way", "rent": 2200, "latitude": 32.76114, "longitude": -117.01184, "beds": 2, "baths": 1, "sqft": 1150},
    {"address": "819 Spruce Way", "rent": 2625, "latitude": 32.76687, "longitude": -117.03163, "beds": 3, "baths": 2, "sqft": 1520},
    # Intentional rent outliers (otherwise a good match)
    {"address": "55 Sublet Ct", "rent": 900, "latitude": 32.77223, "longitude": -117.01866, "beds": 3, "baths": 2, "sqft": 1400},
    {"address": "9 Penthouse Pl", "rent": 8000, "latitude": 32.75912, "longitude": -117.02267, "beds": 3, "baths": 2, "sqft": 1450},
    # Should be removed by the filters
    {"address": "1200 Far Ave", "rent": 2550, "latitude": 32.79345, "longitude": -117.05346, "beds": 3, "baths": 2, "sqft": 1400},
    {"address": "1315 Studio St", "rent": 1500, "latitude": 32.76760, "longitude": -117.01794, "beds": 1, "baths": 1, "sqft": 600},
    {"address": "1420 Mansion Blvd", "rent": 4200, "latitude": 32.75945, "longitude": -117.03499, "beds": 6, "baths": 4, "sqft": 3200},
    {"address": "1525 Tiny Ln", "rent": 1900, "latitude": 32.77354, "longitude": -117.02218, "beds": 3, "baths": 2, "sqft": 900},
    {"address": "1630 Estate Dr", "rent": 3400, "latitude": 32.75829, "longitude": -117.01524, "beds": 3, "baths": 2, "sqft": 2100},
    {"address": "1744 Bath Rd", "rent": 2300, "latitude": 32.76971, "longitude": -117.03318, "beds": 3, "baths": 4, "sqft": 1400},
    {"address": "1850 Remote Rd", "rent": 2450, "latitude": 32.79081, "longitude": -116.97530, "beds": 4, "baths": 2, "sqft": 1500},
    {"address": "1962 Loft Ave", "rent": 1800, "latitude": 32.76091, "longitude": -117.02572, "beds": 1, "baths": 1, "sqft": 700},
]


class MockComparableSource(ComparableDataSource):
    """Static in-memory dataset used for development and tests."""

    def get_comparables(self, subject: SubjectProperty | None = None) -> list[Comparable]:
        """Return a fresh copy so callers can't modify the dataset. `subject` is ignored."""
        return [dict(c) for c in MOCK_COMPARABLES]  # type: ignore[misc]
