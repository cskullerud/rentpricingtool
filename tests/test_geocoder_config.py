import logging
import re
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.valuation import get_geocoder
from app.services.data_sources import ProviderConfigurationError
from app.services.geocoding import (
    CachingGeocoder,
    CensusGeocoder,
    Geocoder,
    GeocoderConfigurationError,
    GeocoderType,
    MockGeocoder,
    build_geocoder,
    describe_geocoder,
    get_geocoder_type,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GEOCODER", "DATA_PROVIDER", "RENTCAST_API_KEY", "GEOCODE_CACHE_TTL_SECONDS",
                 "GEOCODE_NOT_FOUND_TTL_SECONDS", "GEOCODER_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)

    def blocked(*args, **kwargs):
        raise AssertionError("a configuration test tried to use the network")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)


# --- GEOCODER ------------------------------------------------------------------------------------------------

def test_the_default_is_mock():
    assert get_geocoder_type() is GeocoderType.MOCK
    assert isinstance(build_geocoder(), MockGeocoder)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_is_mock(monkeypatch, value):
    monkeypatch.setenv("GEOCODER", value)
    assert get_geocoder_type() is GeocoderType.MOCK


@pytest.mark.parametrize("value", ["census", "CENSUS", " Census "])
def test_census_is_selected_case_insensitively(monkeypatch, value):
    monkeypatch.setenv("GEOCODER", value)
    assert get_geocoder_type() is GeocoderType.CENSUS


@pytest.mark.parametrize("value", ["google", "nominatim", "mapbox", "mock,census", "1"])
def test_unknown_values_are_refused(monkeypatch, value):
    monkeypatch.setenv("GEOCODER", value)
    with pytest.raises(GeocoderConfigurationError, match="GEOCODER") as info:
        get_geocoder_type()
    assert isinstance(info.value, ProviderConfigurationError)
    assert "mock, census" in str(info.value)


def test_the_setting_is_read_on_every_call_not_at_import(monkeypatch):
    assert get_geocoder_type() is GeocoderType.MOCK
    monkeypatch.setenv("GEOCODER", "census")
    assert get_geocoder_type() is GeocoderType.CENSUS
    monkeypatch.delenv("GEOCODER")
    assert get_geocoder_type() is GeocoderType.MOCK


# --- the registry --------------------------------------------------------------------------------------------

def test_census_is_wrapped_in_the_cache(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    geocoder = build_geocoder()
    assert isinstance(geocoder, CachingGeocoder) and isinstance(geocoder, Geocoder)
    assert isinstance(geocoder._inner, CensusGeocoder) and geocoder._namespace == "census"


def test_census_without_the_cache_when_its_ttl_is_zero(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    monkeypatch.setenv("GEOCODE_CACHE_TTL_SECONDS", "0")
    assert type(build_geocoder()) is CensusGeocoder


def test_building_a_geocoder_makes_no_network_call(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    build_geocoder()  # the autouse guard fails the test on any network use


def test_building_passes_the_settings_through(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    monkeypatch.setenv("GEOCODER_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("GEOCODE_CACHE_TTL_SECONDS", "3600")
    geocoder = build_geocoder()
    assert geocoder._inner._settings.timeout_seconds == 4.0 and geocoder._settings.ttl_seconds == 3600.0


def test_invalid_geocoder_settings_stop_the_build(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    monkeypatch.setenv("GEOCODER_TIMEOUT_SECONDS", "abc")
    with pytest.raises(GeocoderConfigurationError, match="GEOCODER_TIMEOUT_SECONDS"):
        build_geocoder()


def test_the_mock_never_uses_the_cache_or_the_census_settings(monkeypatch):
    monkeypatch.setenv("GEOCODER_TIMEOUT_SECONDS", "abc")  # irrelevant for the mock
    monkeypatch.setenv("GEOCODE_CACHE_TTL_SECONDS", "abc")
    assert isinstance(build_geocoder(), MockGeocoder)
    assert describe_geocoder() == ("mock", False)


@pytest.mark.parametrize(
    "env, expected",
    [({}, ("mock", False)), ({"GEOCODER": "mock"}, ("mock", False)), ({"GEOCODER": "census"}, ("census", True)),
     ({"GEOCODER": "census", "GEOCODE_CACHE_TTL_SECONDS": "0"}, ("census", False))],
)
def test_describe_geocoder(monkeypatch, env, expected):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert describe_geocoder() == expected


# --- dependency injection --------------------------------------------------------------------------------------

def test_get_geocoder_is_still_the_router_dependency():
    assert get_geocoder.__module__ == "app.routers.valuation" and get_geocoder.__name__ == "get_geocoder"
    assert isinstance(get_geocoder(), MockGeocoder)


def test_get_geocoder_delegates_to_the_registry(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    assert isinstance(get_geocoder(), CachingGeocoder)


class FixedGeocoder(Geocoder):
    def __init__(self):
        self.calls = []

    def geocode(self, address):
        self.calls.append(address)
        return {"latitude": 32.7678, "longitude": -117.0231}


def test_a_dependency_override_still_wins(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")  # would otherwise build the Census geocoder
    fixed = FixedGeocoder()
    app.dependency_overrides[get_geocoder] = lambda: fixed
    try:
        response = TestClient(app).post("/valuation", json={"address": "9 Anywhere Rd", "beds": 3, "baths": 2, "sqft": 1400})
    finally:
        app.dependency_overrides.pop(get_geocoder, None)
    assert response.status_code == 200 and fixed.calls == ["9 Anywhere Rd"]


def test_the_existing_mock_behaviour_is_unchanged_by_default():
    response = TestClient(app).post("/valuation", json={"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400})
    assert response.status_code == 200 and response.json()["recommended_rent"] == 2512


def test_a_misconfigured_geocoder_gives_a_503_not_a_crash(monkeypatch):
    monkeypatch.setenv("GEOCODER", "google")
    response = TestClient(app).post("/valuation", json={"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400})
    assert response.status_code == 503 and "GEOCODER" not in response.text


# --- startup logging and the warning --------------------------------------------------------------------------------

def startup_log(caplog, **env):
    with caplog.at_level(logging.INFO, logger="app.main"):
        with TestClient(app) as started:  # runs the lifespan (startup)
            assert started.get("/").status_code == 200
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "app.main"]


def test_startup_logs_the_mock_geocoder_by_default(caplog):
    lines = startup_log(caplog)
    assert ("INFO", "Geocoder: mock") in lines and ("INFO", "Geocode cache: disabled") in lines


def test_startup_logs_the_census_geocoder_and_its_cache(caplog, monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    lines = startup_log(caplog)
    assert ("INFO", "Geocoder: census") in lines and ("INFO", "Geocode cache: enabled") in lines


def test_startup_logs_a_disabled_cache(caplog, monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    monkeypatch.setenv("GEOCODE_CACHE_TTL_SECONDS", "0")
    assert ("INFO", "Geocode cache: disabled") in startup_log(caplog)


def test_startup_warns_when_rentcast_is_active_with_the_mock_geocoder(caplog, monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key-not-real")
    lines = startup_log(caplog)
    warnings = [m for level, m in lines if level == "WARNING"]
    assert len(warnings) == 1
    assert warnings[0].startswith("RentCast provider active with mock geocoder")
    assert "GEOCODER=census" in warnings[0]


@pytest.mark.parametrize(
    "env",
    [{}, {"DATA_PROVIDER": "mock"}, {"DATA_PROVIDER": "rentcast", "RENTCAST_API_KEY": "k", "GEOCODER": "census"},
     {"DATA_PROVIDER": "mock", "GEOCODER": "census"}],
)
def test_no_warning_in_the_other_combinations(caplog, monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert [m for level, m in startup_log(caplog) if level == "WARNING"] == []


def test_a_bad_geocoder_setting_is_logged_and_the_app_still_starts(caplog, monkeypatch):
    monkeypatch.setenv("GEOCODER", "google")
    lines = startup_log(caplog)
    assert any(level == "ERROR" and "geocoder is misconfigured" in m for level, m in lines)


def test_startup_makes_no_network_call(monkeypatch, caplog):
    monkeypatch.setenv("GEOCODER", "census")
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key-not-real")
    startup_log(caplog)  # the autouse guard fails the test on any network use


# --- the form's address note --------------------------------------------------------------------------------------------

def test_the_form_explains_demo_mode_with_the_mock_geocoder():
    html = TestClient(app).get("/ui/").text
    assert 'id="geocoder-note"' in html and "demo mode" in html and 'id="address-hint"' not in html


def test_the_form_asks_for_city_state_and_zip_with_a_real_geocoder(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    html = TestClient(app).get("/ui/").text
    assert 'id="address-hint"' in html and "city, state and ZIP code" in html and 'id="geocoder-note"' not in html


def test_the_form_has_no_note_when_the_geocoder_is_misconfigured(monkeypatch):
    monkeypatch.setenv("GEOCODER", "google")
    response = TestClient(app).get("/ui/")
    assert response.status_code == 200
    assert 'id="geocoder-note"' not in response.text and 'id="address-hint"' not in response.text


def test_rendering_the_form_never_runs_a_lookup(monkeypatch):
    monkeypatch.setenv("GEOCODER", "census")
    assert re.search(r'<form id="valuation-form"', TestClient(app).get("/ui/").text)  # guard fails on network use


def test_env_example_documents_the_new_settings():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
    for name in ("GEOCODER", "GEOCODE_CACHE_TTL_SECONDS", "GEOCODER_TIMEOUT_SECONDS"):
        assert name in text
