import os
from enum import Enum

PROVIDER_ENV_VAR = "DATA_PROVIDER"


class ProviderType(str, Enum):
    MOCK = "mock"
    RENTCAST = "rentcast"
    CSV = "csv"


class ProviderConfigurationError(Exception):
    """DATA_PROVIDER names a provider that does not exist."""


def get_provider_type() -> ProviderType:
    """Return the configured provider. Read from the environment on every call, not at import.

    Unset or blank means MOCK. Names are case-insensitive.
    """
    raw = os.getenv(PROVIDER_ENV_VAR, "").strip().lower()
    if not raw:
        return ProviderType.MOCK
    try:
        return ProviderType(raw)
    except ValueError:
        valid = ", ".join(p.value for p in ProviderType)
        raise ProviderConfigurationError(
            f"Unknown {PROVIDER_ENV_VAR} {raw!r}; expected one of: {valid}"
        ) from None
