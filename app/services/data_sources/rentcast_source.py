from app.services.data_sources.base import Comparable, ComparableDataSource


class RentCastComparableSource(ComparableDataSource):
    """Placeholder for the RentCast provider (planned, not implemented).

    When implemented it should call the RentCast API, read its key from the environment
    (never hardcode it), and map each listing to the Comparable shape. Tests must stub the
    HTTP layer: the test suite makes no network calls.
    """

    def get_comparables(self) -> list[Comparable]:
        raise NotImplementedError("The RentCast provider is not implemented yet")
