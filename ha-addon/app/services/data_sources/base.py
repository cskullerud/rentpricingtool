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


class DataSourceError(Exception):
    """A data source could not supply comparables (network failure, bad credentials, ...).

    `status_code` and `public_message` say how the API reports it. The public message is
    deliberately generic: the exception text may carry operational detail and is only logged.
    """

    status_code: int = 502
    public_message: str = "The comparable data provider failed to return data"


class ComparableDataSource(ABC):
    """Where comparable properties come from.

    The valuation engine depends only on this interface, so a provider (mock data, a CSV
    file, a rental-listings API, ...) can be swapped in without touching the engine.
    """

    @abstractmethod
    def get_comparables(self, subject: SubjectProperty | None = None) -> list[Comparable]:
        """Return the comparable properties available for pricing.

        `subject` is optional: a source may use it to narrow its search. A source that holds a
        fixed dataset works without it; one that searches by location may refuse to (and says
        so clearly). Filtering to the subject remains the engine's job (comparables.py).
        """
