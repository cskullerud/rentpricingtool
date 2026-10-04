import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.valuation import get_comparable_source
from app.services.data_sources import (
    ComparableDataSource,
    DataSourceError,
    ProviderConfigurationError,
    RentCastAuthError,
    RentCastComparableSource,
    RentCastRateLimitError,
    RentCastResponseError,
    RentCastSettings,
    RentCastUnavailableError,
)
from app.services.data_sources.rentcast_source import TtlCache

client = TestClient(app)
BODY = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400, "latitude": 32.7678, "longitude": -117.0231}


class FailingSource(ComparableDataSource):
    def __init__(self, error):
        self.error = error

    def get_comparables(self, subject=None):
        raise self.error


def post_with(source):
    app.dependency_overrides[get_comparable_source] = lambda: source
    try:
        return client.post("/valuation", json=BODY)
    finally:
        app.dependency_overrides.pop(get_comparable_source, None)


@pytest.mark.parametrize(
    "error, status",
    [
        (RentCastAuthError("401 for key abc123"), 502),
        (RentCastRateLimitError("429"), 503),
        (RentCastUnavailableError("timed out"), 503),
        (RentCastResponseError("bad json"), 502),
        (DataSourceError("generic"), 502),
    ],
)
def test_data_source_errors_become_http_errors(error, status):
    response = post_with(FailingSource(error))
    assert response.status_code == status
    assert response.json()["detail"] == error.public_message


def test_error_detail_does_not_leak_the_exception_text():
    response = post_with(FailingSource(RentCastAuthError("secret-key-value rejected")))
    assert "secret-key-value" not in response.text


def test_error_is_logged_with_its_detail(caplog):
    with caplog.at_level(logging.WARNING):
        post_with(FailingSource(RentCastUnavailableError("connection refused")))
    assert "connection refused" in caplog.text


def test_misconfiguration_raised_while_building_the_source_is_503():
    def broken():
        raise ProviderConfigurationError("RENTCAST_API_KEY is required for DATA_PROVIDER=rentcast")

    app.dependency_overrides[get_comparable_source] = broken
    try:
        response = client.post("/valuation", json=BODY)
    finally:
        app.dependency_overrides.pop(get_comparable_source, None)
    assert response.status_code == 503
    assert "RENTCAST_API_KEY" not in response.text


def test_rentcast_without_a_key_gives_503_through_the_real_router(monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    response = client.post("/valuation", json=BODY)
    assert response.status_code == 503


def test_rentcast_outage_gives_503_end_to_end():
    def down(url, headers, timeout):
        return 503, b""

    source = RentCastComparableSource(RentCastSettings(api_key="k", max_retries=0), transport=down, cache=TtlCache())
    assert post_with(source).status_code == 503


def test_no_comparables_is_still_404():
    source = RentCastComparableSource(
        RentCastSettings(api_key="k"), transport=lambda u, h, t: (200, b"[]"), cache=TtlCache()
    )
    assert post_with(source).status_code == 404


# --- startup ---------------------------------------------------------------------------

def test_startup_logs_a_misconfigured_provider_but_still_starts(monkeypatch, caplog):
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    with caplog.at_level(logging.INFO), TestClient(app) as started:
        assert started.get("/").status_code == 200
    assert "misconfigured" in caplog.text


def test_startup_logs_the_provider(caplog):
    with caplog.at_level(logging.INFO), TestClient(app):
        pass
    assert "Comparable data provider: mock" in caplog.text
