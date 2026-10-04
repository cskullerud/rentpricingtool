from app.services.data_sources.base import ComparableDataSource
from app.services.data_sources.csv_source import CsvComparableSource
from app.services.data_sources.mock_source import MockComparableSource
from app.services.data_sources.provider_config import ProviderType, get_provider_type
from app.services.data_sources.rentcast_source import RentCastComparableSource


def get_provider() -> ComparableDataSource:
    """Build the data source selected by DATA_PROVIDER (MOCK when unset)."""
    provider = get_provider_type()
    if provider is ProviderType.RENTCAST:
        return RentCastComparableSource()
    if provider is ProviderType.CSV:
        return CsvComparableSource()
    return MockComparableSource()
