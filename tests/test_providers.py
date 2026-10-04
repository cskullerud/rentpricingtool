import pytest

from app.services.data_sources import (
    ComparableDataSource,
    CsvComparableSource,
    MockComparableSource,
    ProviderConfigurationError,
    ProviderType,
    RentCastComparableSource,
    SubjectProperty,
    get_provider,
    get_provider_type,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("DATA_PROVIDER", raising=False)


def test_default_is_mock():
    assert get_provider_type() is ProviderType.MOCK
    assert isinstance(get_provider(), MockComparableSource)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_is_mock(monkeypatch, value):
    monkeypatch.setenv("DATA_PROVIDER", value)
    assert get_provider_type() is ProviderType.MOCK


@pytest.mark.parametrize(
    "value, provider_type, source_class",
    [
        ("mock", ProviderType.MOCK, MockComparableSource),
        ("RentCast", ProviderType.RENTCAST, RentCastComparableSource),
        (" csv ", ProviderType.CSV, CsvComparableSource),
    ],
)
def test_env_selects_provider(monkeypatch, value, provider_type, source_class):
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key")
    monkeypatch.setenv("DATA_PROVIDER", value)
    assert get_provider_type() is provider_type
    source = get_provider()
    assert isinstance(source, source_class)
    assert isinstance(source, ComparableDataSource)


def test_env_is_read_on_every_call(monkeypatch):
    assert get_provider_type() is ProviderType.MOCK
    monkeypatch.setenv("DATA_PROVIDER", "csv")
    assert get_provider_type() is ProviderType.CSV


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "zillow")
    with pytest.raises(ProviderConfigurationError, match="zillow"):
        get_provider_type()


def test_csv_placeholder_is_not_implemented():
    with pytest.raises(NotImplementedError):
        CsvComparableSource().get_comparables()
    with pytest.raises(NotImplementedError):
        CsvComparableSource().get_comparables(SubjectProperty("1 A St", 32.7, -117.0, 3, 2, 1400))


def test_router_delegates_to_registry(monkeypatch):
    from app.routers.valuation import get_comparable_source

    assert isinstance(get_comparable_source(), MockComparableSource)
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key")
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    assert isinstance(get_comparable_source(), RentCastComparableSource)


def test_rentcast_without_a_key_is_a_configuration_error(monkeypatch):
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    with pytest.raises(ProviderConfigurationError, match="RENTCAST_API_KEY"):
        get_provider()
