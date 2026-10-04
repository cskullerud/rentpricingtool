from abc import ABC, abstractmethod
from typing import TypedDict

from app.services.data_sources.base import DataSourceError


class Coordinates(TypedDict):
    latitude: float  # decimal degrees
    longitude: float  # decimal degrees


class AddressNotFoundError(Exception):
    """The geocoder could not turn an address into coordinates (unknown, blank or ambiguous)."""

    def __init__(self, address: str, reason: str = "address not found; check it and include the city and state"):
        self.address = address
        self.reason = reason
        super().__init__(
            f"Could not geocode address {address!r}: {reason}. "
            "Or supply latitude and longitude to skip geocoding."
        )


class GeocoderUnavailableError(DataSourceError):
    """The address lookup service could not be reached, timed out, or is busy or down.

    Not the same as AddressNotFoundError: the address may be fine. Retrying later may work.
    """

    status_code = 503
    public_message = "The address lookup service is unavailable; try again shortly, or enter exact coordinates"


class GeocoderResponseError(DataSourceError):
    """The address lookup service answered, but not with something this app understands."""

    status_code = 502
    public_message = "The address lookup service returned an unexpected response; try again later, or enter exact coordinates"


class Geocoder(ABC):
    """Turns a street address into coordinates.

    The valuation engine depends only on this interface, so a real provider (Google, ...)
    can replace the mock without changing the engine.
    """

    @abstractmethod
    def geocode(self, address: str) -> Coordinates:
        """Return the coordinates of `address`, or raise AddressNotFoundError."""
