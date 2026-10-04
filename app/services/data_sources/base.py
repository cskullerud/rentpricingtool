from abc import ABC, abstractmethod
from typing import TypedDict


class Comparable(TypedDict):
    address: str
    rent: int
    latitude: float  # decimal degrees
    longitude: float  # decimal degrees
    beds: int
    baths: float
    sqft: int


class ComparableDataSource(ABC):
    """Where comparable properties come from.

    The valuation engine depends only on this interface, so a provider (mock data, a CSV
    file, a rental-listings API, ...) can be swapped in without touching the engine.
    """

    @abstractmethod
    def get_comparables(self) -> list[Comparable]:
        """Return the comparable properties available for pricing."""
