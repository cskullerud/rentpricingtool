from app.services.data_sources.base import Comparable, ComparableDataSource


class CsvComparableSource(ComparableDataSource):
    """Placeholder for the CSV provider (planned, not implemented).

    When implemented it should load comparables from a CSV file with columns address, rent,
    latitude, longitude, beds, baths, sqft; validate each row and report malformed rows
    rather than skipping them silently; and take the file path as a constructor argument.
    """

    def get_comparables(self) -> list[Comparable]:
        raise NotImplementedError("The CSV provider is not implemented yet")
