"""Which geocoder to use, and its settings. Read from the environment on every call, not at import."""
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from app.services.data_sources.provider_config import ProviderConfigurationError

GEOCODER_ENV_VAR = "GEOCODER"

DAY = 86400.0


class GeocoderType(str, Enum):
    MOCK = "mock"
    CENSUS = "census"


class GeocoderConfigurationError(ProviderConfigurationError):
    """GEOCODER names a geocoder that does not exist, or a geocoder setting is invalid."""


def get_geocoder_type() -> GeocoderType:
    """The configured geocoder. Unset or blank means MOCK, so development and the tests need no setup."""
    raw = os.getenv(GEOCODER_ENV_VAR, "").strip().lower()
    if not raw:
        return GeocoderType.MOCK
    try:
        return GeocoderType(raw)
    except ValueError:
        valid = ", ".join(g.value for g in GeocoderType)
        raise GeocoderConfigurationError(f"Unknown {GEOCODER_ENV_VAR} {raw!r}; expected one of: {valid}") from None


def env_number(env: Mapping[str, str], name: str, default, kind, low, high):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise GeocoderConfigurationError(f"{name} must be a number, got {raw!r}") from None
    if not low <= value <= high:
        raise GeocoderConfigurationError(f"{name} must be between {low} and {high}, got {value}")
    return value


@dataclass(frozen=True)
class GeocodeCacheSettings:
    ttl_seconds: float = 90 * DAY  # how long a found address is remembered; 0 turns the cache off
    not_found_ttl_seconds: float = 1 * DAY  # how long "not found" is remembered; 0 turns that off

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GeocodeCacheSettings":
        env = os.environ if env is None else env
        return cls(
            ttl_seconds=env_number(env, "GEOCODE_CACHE_TTL_SECONDS", cls.ttl_seconds, float, 0, 365 * DAY),
            not_found_ttl_seconds=env_number(env, "GEOCODE_NOT_FOUND_TTL_SECONDS", cls.not_found_ttl_seconds, float, 0, 30 * DAY),
        )
