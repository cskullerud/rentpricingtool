"""Remember geocoding answers in the application's SQLite database.

Addresses do not move, so a found address is kept for a long time (90 days by default). That
saves lookups, keeps already-seen addresses working while the public service is down, and keeps
the load on a free service low. Entries live in the existing `provider_cache` table under keys
that start with "geocode", so there is no schema change.

Best effort, like the RentCast cache: if the database is unavailable the lookup is simply made.
"""
import logging
import math

from app.services.geocoding.base import AddressNotFoundError, Coordinates, Geocoder
from app.services.geocoding.geocoder_config import GeocodeCacheSettings
from app.services.geocoding.normalize import cache_key

logger = logging.getLogger(__name__)


def _default_cache():
    """The persistent cache in the application database (resolved on first use)."""
    from app.persistence import SqliteCache, get_database_manager

    return SqliteCache(get_database_manager())


def _valid_coordinates(value) -> bool:
    if not isinstance(value, dict):
        return False
    latitude, longitude = value.get("latitude"), value.get("longitude")
    return all(
        isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number)
        for number in (latitude, longitude)
    ) and -90 <= latitude <= 90 and -180 <= longitude <= 180


class CachingGeocoder(Geocoder):
    """Wraps another Geocoder with a persistent cache.

    Found addresses and "address not found" answers are cached (the latter briefly). Failures of
    the service (unavailable, unexpected response) are never cached. A damaged cache entry is
    ignored.
    """

    def __init__(
        self,
        inner: Geocoder,
        namespace: str,
        settings: GeocodeCacheSettings | None = None,
        cache=None,
    ):
        self._inner = inner
        self._namespace = namespace  # keeps answers from different geocoders apart
        self._settings = settings if settings is not None else GeocodeCacheSettings.from_env()
        self._cache = cache  # None means the persistent default, resolved on first use

    def geocode(self, address: str) -> Coordinates:
        normalized = cache_key(address or "")
        if not normalized:
            return self._inner.geocode(address)  # empty input: let the geocoder refuse it, uncached
        if self._cache is None:
            self._cache = _default_cache()
        key = ["geocode", self._namespace, normalized]

        cached = self._cache.get(key)
        if _valid_coordinates(cached):
            logger.info("geocode result=match cache=hit")
            return {"latitude": cached["latitude"], "longitude": cached["longitude"]}
        if isinstance(cached, dict) and cached.get("not_found") is True and isinstance(cached.get("reason"), str):
            logger.info("geocode result=no_match cache=hit")
            raise AddressNotFoundError(address, cached["reason"])

        try:
            found = self._inner.geocode(address)
        except AddressNotFoundError as exc:
            if self._settings.not_found_ttl_seconds > 0:
                self._cache.set(key, {"not_found": True, "reason": exc.reason}, self._settings.not_found_ttl_seconds)
            raise
        if self._settings.ttl_seconds > 0:
            self._cache.set(key, {"latitude": found["latitude"], "longitude": found["longitude"]}, self._settings.ttl_seconds)
        return found
