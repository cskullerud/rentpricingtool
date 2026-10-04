from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypedDict


class Comparable(TypedDict):
    address: str
    rent: int
    latitude: float  # decimal degrees
    longitude: float  # decimal degrees
    beds: int
    baths: float
    sqft: int


@dataclass(frozen=True)
class SubjectProperty:
    """The property being priced, with its location already resolved.

    Passed to a data source so a provider that searches by location (a listings API) knows
    where to look. Sources that hold a fixed dataset can ignore it.
    """

    address: str
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
    def get_comparables(self, subject: SubjectProperty | None = None) -> list[Comparable]:
        """Return the comparable properties available for pricing.

        `subject` is optional: a source may use it to narrow its search, and must still work
        without it. Filtering to the subject remains the engine's job (comparables.py).
        """
