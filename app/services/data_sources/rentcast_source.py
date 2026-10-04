import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.services.data_sources.base import (
    Comparable,
    ComparableDataSource,
    DataSourceError,
    SubjectProperty,
)
from app.services.data_sources.provider_config import ProviderConfigurationError

logger = logging.getLogger(__name__)

LISTINGS_PATH = "/v1/listings/rental/long-term"

# (url, headers, timeout_seconds) -> (http_status, response_body). Raises
# RentCastUnavailableError if the server can't be reached. Tests inject a fake one.
Transport = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]


class RentCastAuthError(DataSourceError):
    """RentCast rejected the API key (HTTP 401 or 403)."""

    status_code = 502
    public_message = "The comparable data provider rejected our credentials"


class RentCastRateLimitError(DataSourceError):
    """RentCast says the rate limit was exceeded (HTTP 429)."""

    status_code = 503
    public_message = "The comparable data provider is busy; try again shortly"


class RentCastUnavailableError(DataSourceError):
    """RentCast could not be reached, timed out, or answered with a server error."""

    status_code = 503
    public_message = "The comparable data provider is unavailable; try again shortly"


class RentCastResponseError(DataSourceError):
    """RentCast answered, but not with something this source understands."""


def _env_number(env: Mapping[str, str], name: str, default, kind, low, high):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise ProviderConfigurationError(f"{name} must be a number, got {raw!r}") from None
    if not low <= value <= high:
        raise ProviderConfigurationError(f"{name} must be between {low} and {high}, got {value}")
    return value


@dataclass(frozen=True)
class RentCastSettings:
    api_key: str = field(repr=False)  # never shown in logs or reprs
    base_url: str = "https://api.rentcast.io"
    radius_miles: float = 5.0  # RentCast allows at most 100
    limit: int = 100  # listings per request; RentCast allows 1-500
    timeout_seconds: float = 10.0
    cache_ttl_seconds: float = 86400.0  # 0 turns caching off
    max_retries: int = 1  # extra attempts after a 429, 5xx or network failure
    retry_delay_seconds: float = 0.5  # doubles after each retry

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RentCastSettings":
        """Read settings from the environment (each call, not at import).

        Raises ProviderConfigurationError if RENTCAST_API_KEY is missing or a value is invalid.
        """
        env = os.environ if env is None else env
        api_key = env.get("RENTCAST_API_KEY", "").strip()
        if not api_key:
            raise ProviderConfigurationError("RENTCAST_API_KEY is required for DATA_PROVIDER=rentcast")
        base_url = env.get("RENTCAST_BASE_URL", "").strip() or cls.base_url
        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            radius_miles=_env_number(env, "RENTCAST_RADIUS_MILES", cls.radius_miles, float, 0.1, 100),
            limit=_env_number(env, "RENTCAST_LIMIT", cls.limit, int, 1, 500),
            timeout_seconds=_env_number(env, "RENTCAST_TIMEOUT_SECONDS", cls.timeout_seconds, float, 0.1, 120),
            cache_ttl_seconds=_env_number(env, "RENTCAST_CACHE_TTL_SECONDS", cls.cache_ttl_seconds, float, 0, 604800),
            max_retries=_env_number(env, "RENTCAST_MAX_RETRIES", cls.max_retries, int, 0, 3),
            retry_delay_seconds=_env_number(env, "RENTCAST_RETRY_DELAY_SECONDS", cls.retry_delay_seconds, float, 0, 10),
        )


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """The real HTTP call. The only place in this module that touches the network."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RentCastUnavailableError(f"Could not reach RentCast: {exc}") from exc


class TtlCache:
    """A small thread-safe in-memory cache whose entries expire."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._items: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires, value = entry
            if self._clock() >= expires:
                del self._items[key]
                return None
            return value

    def set(self, key, value, ttl: float) -> None:
        with self._lock:
            self._items[key] = (self._clock() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


# Shared between instances: the router builds a new source for every request, and every
# uncached lookup costs a billable RentCast call.
_SHARED_CACHE = TtlCache()


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _to_comparable(listing) -> Comparable | None:
    """Map one RentCast listing to a Comparable, or None if any required field is missing."""
    if not isinstance(listing, dict):
        return None
    address = listing.get("formattedAddress")
    price = _number(listing.get("price"))
    latitude = _number(listing.get("latitude"))
    longitude = _number(listing.get("longitude"))
    bedrooms = _number(listing.get("bedrooms"))
    bathrooms = _number(listing.get("bathrooms"))
    sqft = _number(listing.get("squareFootage"))
    if not isinstance(address, str) or not address.strip():
        return None
    if None in (price, latitude, longitude, bedrooms, bathrooms, sqft):
        return None
    if price <= 0 or sqft <= 0 or bedrooms < 0 or bathrooms < 0:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return {
        "address": address,
        "rent": round(price),
        "latitude": latitude,
        "longitude": longitude,
        "beds": int(bedrooms),
        "baths": bathrooms,
        "sqft": round(sqft),
    }


class RentCastComparableSource(ComparableDataSource):
    """Active long-term rental listings near the subject, from the RentCast API.

    Makes one GET to the listings endpoint per uncached subject location and returns the
    listings that have every field a Comparable needs. Filtering to the subject and the
    statistics stay in the engine. Needs the subject's location, so `subject` is required.

    `transport` and `cache` can be injected; tests do, so they never touch the network.
    """

    def __init__(
        self,
        settings: RentCastSettings | None = None,
        transport: Transport = urllib_transport,
        cache: TtlCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._settings = settings if settings is not None else RentCastSettings.from_env()
        self._transport = transport
        self._cache = cache if cache is not None else _SHARED_CACHE
        self._sleep = sleep

    def get_comparables(self, subject: SubjectProperty | None = None) -> list[Comparable]:
        if subject is None:
            raise ValueError("RentCastComparableSource needs the subject's location")
        settings = self._settings
        query = {
            "latitude": f"{subject.latitude:.5f}",
            "longitude": f"{subject.longitude:.5f}",
            "radius": f"{settings.radius_miles:g}",
            "limit": str(settings.limit),
            "status": "Active",
        }
        # Locations about 100 m apart share an entry; the key has no API key in it.
        key = (
            settings.base_url,
            round(subject.latitude, 3),
            round(subject.longitude, 3),
            settings.radius_miles,
            settings.limit,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return [dict(c) for c in cached]  # type: ignore[misc]

        url = f"{settings.base_url}{LISTINGS_PATH}?{urllib.parse.urlencode(query)}"
        headers = {"X-Api-Key": settings.api_key, "Accept": "application/json"}
        started = time.monotonic()
        listings = self._fetch(url, headers)
        logger.info("RentCast: %d listings in %.0f ms", len(listings), (time.monotonic() - started) * 1000)

        comparables = [c for c in map(_to_comparable, listings) if c is not None]
        skipped = len(listings) - len(comparables)
        if skipped:
            logger.warning("RentCast: skipped %d of %d listings with missing fields", skipped, len(listings))
        if settings.cache_ttl_seconds > 0:
            self._cache.set(key, comparables, settings.cache_ttl_seconds)
        return [dict(c) for c in comparables]  # type: ignore[misc]

    def _fetch(self, url: str, headers: Mapping[str, str]) -> list:
        """Call RentCast, retrying only transient failures (429, 5xx, network) with backoff."""
        settings = self._settings
        delay = settings.retry_delay_seconds
        for attempt in range(settings.max_retries + 1):
            try:
                status, body = self._transport(url, headers, settings.timeout_seconds)
                return self._parse(status, body)
            except (RentCastRateLimitError, RentCastUnavailableError) as exc:
                if attempt == settings.max_retries:
                    raise
                logger.warning("RentCast attempt %d failed (%s); retrying in %.1f s", attempt + 1, exc, delay)
                self._sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")  # the loop always returns or raises

    @staticmethod
    def _parse(status: int, body: bytes) -> list:
        if status in (401, 403):
            raise RentCastAuthError(f"RentCast rejected the API key (HTTP {status})")
        if status == 429:
            raise RentCastRateLimitError("RentCast rate limit exceeded (HTTP 429)")
        if status >= 500:
            raise RentCastUnavailableError(f"RentCast server error (HTTP {status})")
        if status != 200:
            raise RentCastResponseError(f"Unexpected RentCast response (HTTP {status})")
        try:
            data = json.loads(body)
        except ValueError:
            raise RentCastResponseError("RentCast returned a response that is not valid JSON") from None
        if not isinstance(data, list):
            raise RentCastResponseError("RentCast returned JSON that is not a list of listings")
        return data
