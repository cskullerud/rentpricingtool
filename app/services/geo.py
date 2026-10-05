"""Great-circle distance between latitude/longitude points."""
import math

EARTH_RADIUS_KM = 6371.0088  # mean Earth radius
KM_PER_MILE = 1.609344


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometers (haversine formula).

    Coordinates are decimal degrees. Assumes a spherical Earth, which is accurate to
    roughly 0.5% -- plenty for "is this comparable close enough".
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def miles_between_points(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in miles."""
    return haversine_distance(lat1, lon1, lat2, lon2) / KM_PER_MILE


def nearest_distance_miles(latitude: float, longitude: float, points) -> float | None:
    """Miles from the point to the closest of `points` (each a mapping with "latitude" and
    "longitude"), or None if there are none. Kept here, with no other app imports, so both the
    comparable filters and the data sources can use it without importing each other."""
    distances = [miles_between_points(latitude, longitude, p["latitude"], p["longitude"]) for p in points]
    return min(distances) if distances else None
