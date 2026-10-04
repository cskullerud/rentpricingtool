"""Build the configured geocoder. The one place that names concrete geocoders."""
from app.services.geocoding.base import Geocoder
from app.services.geocoding.caching import CachingGeocoder
from app.services.geocoding.census_geocoder import CensusGeocoder, CensusSettings
from app.services.geocoding.geocoder_config import GeocodeCacheSettings, GeocoderType, get_geocoder_type
from app.services.geocoding.mock_geocoder import MockGeocoder


def build_geocoder() -> Geocoder:
    """The geocoder selected by GEOCODER (mock when unset), wrapped in the cache when it is on.

    Reads the environment on every call. Building one makes no network call.
    """
    if get_geocoder_type() is GeocoderType.MOCK:
        return MockGeocoder()
    census = CensusGeocoder(CensusSettings.from_env())
    cache = GeocodeCacheSettings.from_env()
    if not cache.enabled:
        return census
    return CachingGeocoder(census, namespace="census", settings=cache)


def describe_geocoder() -> tuple[str, bool]:
    """(geocoder name, whether answers are cached), for the startup log. The mock is never cached."""
    geocoder = get_geocoder_type()
    if geocoder is GeocoderType.MOCK:
        return geocoder.value, False
    return geocoder.value, GeocodeCacheSettings.from_env().enabled
