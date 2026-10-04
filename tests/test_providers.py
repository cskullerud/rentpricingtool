import pytest

from app.services.data_sources import (
    ComparableDataSource,
    CsvComparableSource,
    MockComparableSource,
    ProviderConfigurationError,
    ProviderType,
    RentCastComparableSource,
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


@pytest.mark.parametrize("source_class", [RentCastComparableSource, CsvComparableSource])
def test_placeholder_sources_are_not_implemented(source_class):
    with pytest.raises(NotImplementedError):
        source_class().get_comparables()


def test_router_delegates_to_registry(monkeypatch):
    from app.routers.valuation import get_comparable_source

    assert isinstance(get_comparable_source(), MockComparableSource)
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    assert isinstance(get_comparable_source(), RentCastComparableSource)
