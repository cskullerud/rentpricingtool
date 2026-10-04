"""Address -> coordinates with the US Census Bureau geocoder (free, US addresses only).

One GET to the one-line-address service per lookup. It places an address on its street by
interpolating the street's address range, so positions are accurate to roughly tens of metres:
plenty for choosing comparables within a mile, not a rooftop. There is no service-level
guarantee, so failures are reported as GeocoderUnavailableError (try again) rather than as
"address not found", and the CachingGeocoder remembers answers.

The HTTP call is an injectable `transport`, so tests never touch the network.
"""
import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.config import VERSION
from app.services.geo import miles_between_points
from app.services.geocoding.base import (
    AddressNotFoundError,
    Coordinates,
    Geocoder,
    GeocoderResponseError,
    GeocoderUnavailableError,
)
from app.services.geocoding.geocoder_config import env_number
from app.services.geocoding.normalize import strip_unit

logger = logging.getLogger(__name__)

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
BENCHMARK = "Public_AR_Current"  # the current public address ranges
MAX_ADDRESS_LENGTH = 200
# Several matches this far apart are different places (the same street in two towns).
AMBIGUITY_MILES = 0.5

# (url, headers, timeout_seconds) -> (http_status, response_body). Raises GeocoderUnavailableError
# if the server cannot be reached. Tests inject a fake one.
Transport = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """The real HTTP call. The only place in this module that touches the network."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GeocoderUnavailableError(f"Could not reach the Census geocoder: {exc}") from exc


@dataclass(frozen=True)
class CensusSettings:
    base_url: str = CENSUS_URL
    benchmark: str = BENCHMARK
    timeout_seconds: float = 10.0
    max_retries: int = 1  # extra attempts after a busy, down or unreachable service
    retry_delay_seconds: float = 0.5  # doubles after each retry
    user_agent: str = f"rentpricingtool/{VERSION}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CensusSettings":
        import os

        env = os.environ if env is None else env
        return cls(
            timeout_seconds=env_number(env, "GEOCODER_TIMEOUT_SECONDS", cls.timeout_seconds, float, 0.1, 60),
            max_retries=env_number(env, "GEOCODER_MAX_RETRIES", cls.max_retries, int, 0, 3),
            retry_delay_seconds=env_number(env, "GEOCODER_RETRY_DELAY_SECONDS", cls.retry_delay_seconds, float, 0, 10),
        )


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


class CensusGeocoder(Geocoder):
    def __init__(
        self,
        settings: CensusSettings | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._settings = settings if settings is not None else CensusSettings.from_env()
        self._transport = transport if transport is not None else urllib_transport
        self._sleep = sleep

    def geocode(self, address: str) -> Coordinates:
        query = strip_unit(address or "")  # unit numbers only get in the way of a street geocoder
        if not query:
            raise AddressNotFoundError(address, "address is empty")
        if len(query) > MAX_ADDRESS_LENGTH:
            raise AddressNotFoundError(address, "address is too long")

        parameters = {"address": query, "benchmark": self._settings.benchmark, "format": "json"}
        url = f"{self._settings.base_url}?{urllib.parse.urlencode(parameters)}"
        started = time.monotonic()
        body = self._fetch(url)
        points = self._parse(body)
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info("geocode source=census result=%s matches=%d ms=%.0f", "match" if points else "no_match", len(points), elapsed_ms)

        if not points:
            raise AddressNotFoundError(address)
        for first in points:
            for second in points:
                if miles_between_points(*first, *second) > AMBIGUITY_MILES:
                    raise AddressNotFoundError(address, "address is ambiguous; include the city, state and ZIP code")
        latitude, longitude = points[0]
        return {"latitude": round(latitude, 6), "longitude": round(longitude, 6)}

    # ---- HTTP, with retries for a busy or unreachable service --------------------------------

    def _fetch(self, url: str) -> bytes:
        settings = self._settings
        headers = {"User-Agent": settings.user_agent, "Accept": "application/json"}
        delay = settings.retry_delay_seconds
        for attempt in range(settings.max_retries + 1):
            try:
                status, body = self._transport(url, headers, settings.timeout_seconds)
                return self._checked(status, body)
            except GeocoderUnavailableError as exc:
                if attempt == settings.max_retries:
                    raise
                logger.warning("Census geocoder attempt %d failed (%s); retrying in %.1f s", attempt + 1, exc, delay)
                self._sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")  # the loop always returns or raises

    @staticmethod
    def _checked(status: int, body: bytes) -> bytes:
        if status == 200:
            if body.lstrip()[:1] == b"<":  # an HTML "busy" or maintenance page instead of JSON
                raise GeocoderUnavailableError("The Census geocoder returned a web page instead of data")
            return body
        if status in (408, 429) or status >= 500:
            raise GeocoderUnavailableError(f"The Census geocoder is busy or down (HTTP {status})")
        raise GeocoderResponseError(f"Unexpected Census geocoder response (HTTP {status})")

    # ---- reading the answer ----------------------------------------------------------------------

    @staticmethod
    def _parse(body: bytes) -> list[tuple[float, float]]:
        """The (latitude, longitude) of each match. Census gives x = longitude and y = latitude."""
        try:
            data = json.loads(body)
        except ValueError:
            raise GeocoderResponseError("The Census geocoder returned a response that is not valid JSON") from None
        result = data.get("result") if isinstance(data, dict) else None
        matches = result.get("addressMatches") if isinstance(result, dict) else None
        if not isinstance(matches, list):
            raise GeocoderResponseError("The Census geocoder response has no list of address matches")
        points = []
        for match in matches:
            coordinates = match.get("coordinates") if isinstance(match, dict) else None
            if not isinstance(coordinates, dict):
                raise GeocoderResponseError("A Census geocoder match has no coordinates")
            longitude, latitude = _number(coordinates.get("x")), _number(coordinates.get("y"))
            if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise GeocoderResponseError("A Census geocoder match has invalid coordinates")
            points.append((latitude, longitude))
        return points
