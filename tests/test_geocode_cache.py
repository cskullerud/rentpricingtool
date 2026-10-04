import json
import logging

import pytest

from app.persistence import DatabaseManager, PersistenceError, SqliteCache
from app.services.geocoding import (
    AddressNotFoundError,
    CachingGeocoder,
    GeocodeCacheSettings,
    Geocoder,
    GeocoderResponseError,
    GeocoderUnavailableError,
)

ADDRESS = "123 Main St, San Diego, CA 92101"
FOUND = {"latitude": 32.7157, "longitude": -117.1611}
DAY = 86400.0


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class ScriptedGeocoder(Geocoder):
    """Answers from a script (a coordinates dict, or an exception to raise) and counts calls."""

    def __init__(self, *script):
        self.script = list(script) or [dict(FOUND)]
        self.calls = []

    def geocode(self, address):
        self.calls.append(address)
        step = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return dict(step)


class BrokenDatabase:
    def connection(self):
        raise PersistenceError("database is down")


@pytest.fixture
def database(tmp_path):
    return DatabaseManager(tmp_path / "geocode.db")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(database, clock):
    return SqliteCache(database, clock=clock)


def make(inner, cache, ttl=90 * DAY, not_found_ttl=DAY, namespace="census"):
    return CachingGeocoder(inner, namespace, GeocodeCacheSettings(ttl, not_found_ttl), cache=cache)


def stored_rows(database):
    with database.connection() as conn:
        return [(row["cache_key"], json.loads(row["value"]), row["expires_at"] - row["created_at"])
                for row in conn.execute("SELECT * FROM provider_cache ORDER BY cache_key")]


# --- hits and misses ---------------------------------------------------------------------------------------

def test_is_a_geocoder(cache):
    assert isinstance(make(ScriptedGeocoder(), cache), Geocoder)


def test_a_miss_calls_the_geocoder_and_a_hit_does_not(cache):
    inner = ScriptedGeocoder()
    geocoder = make(inner, cache)
    assert geocoder.geocode(ADDRESS) == FOUND
    assert geocoder.geocode(ADDRESS) == FOUND
    assert geocoder.geocode(ADDRESS) == FOUND
    assert len(inner.calls) == 1


def test_a_different_address_is_a_miss(cache):
    inner = ScriptedGeocoder()
    geocoder = make(inner, cache)
    geocoder.geocode(ADDRESS)
    geocoder.geocode("124 Main St, San Diego, CA 92101")
    geocoder.geocode("123 Main St, San Diego, CA 92102")
    assert len(inner.calls) == 3


@pytest.mark.parametrize(
    "variant",
    ["123 MAIN ST, SAN DIEGO, CA 92101", "123 Main St Apt 4B, San Diego, CA 92101", "123 Main St #7, San Diego, CA 92101",
     "123 Main St., San Diego, California 92101", "  123   main st ,san diego,  ca 92101 "],
)
def test_spellings_of_one_address_share_one_entry(cache, variant):
    inner = ScriptedGeocoder()
    geocoder = make(inner, cache)
    geocoder.geocode(ADDRESS)
    assert geocoder.geocode(variant) == FOUND
    assert len(inner.calls) == 1


def test_a_different_namespace_is_a_miss(cache):
    first, second = ScriptedGeocoder(), ScriptedGeocoder()
    make(first, cache, namespace="census").geocode(ADDRESS)
    make(second, cache, namespace="other").geocode(ADDRESS)
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_callers_cannot_modify_cached_data(cache):
    geocoder = make(ScriptedGeocoder(), cache)
    geocoder.geocode(ADDRESS)["latitude"] = 0
    assert geocoder.geocode(ADDRESS) == FOUND


def test_what_is_stored_is_the_normalized_key_and_the_coordinates(cache, database):
    make(ScriptedGeocoder(), cache).geocode("123 Main St Apt 4B, San Diego, CA 92101")
    [(key, value, ttl)] = stored_rows(database)
    assert json.loads(key) == ["geocode", "census", "123 main st, san diego, ca 92101"]
    assert value == FOUND and ttl == 90 * DAY


# --- expiry ----------------------------------------------------------------------------------------------------------

def test_found_entries_expire_after_their_ttl(cache, clock):
    inner = ScriptedGeocoder()
    geocoder = make(inner, cache, ttl=3600)
    geocoder.geocode(ADDRESS)
    clock.now += 3599
    geocoder.geocode(ADDRESS)
    assert len(inner.calls) == 1
    clock.now += 2
    geocoder.geocode(ADDRESS)
    assert len(inner.calls) == 2


def test_the_default_lifetime_is_ninety_days(cache, clock):
    inner = ScriptedGeocoder()
    geocoder = CachingGeocoder(inner, "census", GeocodeCacheSettings(), cache=cache)
    geocoder.geocode(ADDRESS)
    clock.now += 90 * DAY - 1
    geocoder.geocode(ADDRESS)
    assert len(inner.calls) == 1
    clock.now += 2
    geocoder.geocode(ADDRESS)
    assert len(inner.calls) == 2


def test_ttl_zero_never_writes_to_the_cache(cache, database):
    inner = ScriptedGeocoder()
    geocoder = make(inner, cache, ttl=0)
    geocoder.geocode(ADDRESS)
    geocoder.geocode(ADDRESS)
    assert len(inner.calls) == 2 and stored_rows(database) == []


# --- "not found" ------------------------------------------------------------------------------------------------------

def test_not_found_is_remembered_with_its_reason(cache):
    inner = ScriptedGeocoder(AddressNotFoundError(ADDRESS, "address is ambiguous; include the city, state and ZIP code"))
    geocoder = make(inner, cache)
    with pytest.raises(AddressNotFoundError):
        geocoder.geocode(ADDRESS)
    with pytest.raises(AddressNotFoundError) as info:
        geocoder.geocode(ADDRESS)
    assert len(inner.calls) == 1
    assert info.value.reason == "address is ambiguous; include the city, state and ZIP code"
    assert info.value.address == ADDRESS


def test_not_found_uses_the_requesting_spelling_in_the_message(cache):
    inner = ScriptedGeocoder(AddressNotFoundError(ADDRESS))
    geocoder = make(inner, cache)
    with pytest.raises(AddressNotFoundError):
        geocoder.geocode(ADDRESS)
    with pytest.raises(AddressNotFoundError) as info:
        geocoder.geocode("123 MAIN ST APT 9, SAN DIEGO, CA 92101")
    assert info.value.address == "123 MAIN ST APT 9, SAN DIEGO, CA 92101"


def test_not_found_expires_much_sooner(cache, clock):
    inner = ScriptedGeocoder(AddressNotFoundError(ADDRESS), dict(FOUND))
    geocoder = make(inner, cache, not_found_ttl=600)
    with pytest.raises(AddressNotFoundError):
        geocoder.geocode(ADDRESS)
    clock.now += 601  # the address may have been added to the data since
    assert geocoder.geocode(ADDRESS) == FOUND
    assert len(inner.calls) == 2


def test_not_found_ttl_zero_means_not_found_is_not_cached(cache, database):
    inner = ScriptedGeocoder(AddressNotFoundError(ADDRESS))
    geocoder = make(inner, cache, not_found_ttl=0)
    for _ in range(2):
        with pytest.raises(AddressNotFoundError):
            geocoder.geocode(ADDRESS)
    assert len(inner.calls) == 2 and stored_rows(database) == []


def test_a_found_address_is_not_affected_by_the_not_found_setting(cache):
    inner = ScriptedGeocoder()
    geocoder = make(inner, cache, not_found_ttl=0)
    geocoder.geocode(ADDRESS)
    geocoder.geocode(ADDRESS)
    assert len(inner.calls) == 1


# --- failures are never cached ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [GeocoderUnavailableError("down"), GeocoderResponseError("garbage"), RuntimeError("bug")])
def test_service_failures_are_not_cached(cache, database, error):
    inner = ScriptedGeocoder(error, dict(FOUND))
    geocoder = make(inner, cache)
    with pytest.raises(type(error)):
        geocoder.geocode(ADDRESS)
    assert stored_rows(database) == []
    assert geocoder.geocode(ADDRESS) == FOUND  # the next try goes to the service again
    assert len(inner.calls) == 2


def test_an_outage_does_not_disturb_addresses_already_cached(cache):
    inner = ScriptedGeocoder(dict(FOUND), GeocoderUnavailableError("down"))
    geocoder = make(inner, cache)
    geocoder.geocode(ADDRESS)
    assert geocoder.geocode(ADDRESS) == FOUND  # served from the cache while the service is down
    with pytest.raises(GeocoderUnavailableError):
        geocoder.geocode("999 Other St, San Diego, CA")


@pytest.mark.parametrize("address", ["", "   ", None, "Apt 4B"])
def test_empty_addresses_go_straight_to_the_geocoder_uncached(cache, database, address):
    inner = ScriptedGeocoder(AddressNotFoundError(address, "address is empty"))
    with pytest.raises(AddressNotFoundError, match="address is empty"):
        make(inner, cache).geocode(address)
    assert stored_rows(database) == []


# --- persistence across restarts -----------------------------------------------------------------------------------------

def test_entries_survive_a_restart(tmp_path, clock):
    path = tmp_path / "persist.db"
    first = ScriptedGeocoder()
    make(first, SqliteCache(DatabaseManager(path), clock=clock)).geocode(ADDRESS)
    # a restart builds new objects over the same file
    second = ScriptedGeocoder(GeocoderUnavailableError("service is down after the restart"))
    assert make(second, SqliteCache(DatabaseManager(path), clock=clock)).geocode(ADDRESS) == FOUND
    assert second.calls == []


def test_not_found_survives_a_restart(tmp_path, clock):
    path = tmp_path / "persist.db"
    first = ScriptedGeocoder(AddressNotFoundError(ADDRESS))
    with pytest.raises(AddressNotFoundError):
        make(first, SqliteCache(DatabaseManager(path), clock=clock)).geocode(ADDRESS)
    second = ScriptedGeocoder()
    with pytest.raises(AddressNotFoundError):
        make(second, SqliteCache(DatabaseManager(path), clock=clock)).geocode(ADDRESS)
    assert second.calls == []


def test_entries_expire_across_a_restart(tmp_path, clock):
    path = tmp_path / "persist.db"
    make(ScriptedGeocoder(), SqliteCache(DatabaseManager(path), clock=clock), ttl=60).geocode(ADDRESS)
    clock.now += 61
    again = ScriptedGeocoder()
    make(again, SqliteCache(DatabaseManager(path), clock=clock), ttl=60).geocode(ADDRESS)
    assert len(again.calls) == 1


# --- damaged cache contents and a broken database -----------------------------------------------------------------------

KEY = ["geocode", "census", "123 main st, san diego, ca 92101"]


@pytest.mark.parametrize(
    "junk",
    ["text", 5, [], {}, {"latitude": 1}, {"latitude": "1", "longitude": "2"}, {"latitude": 95, "longitude": 0},
     {"latitude": 0, "longitude": 181}, {"latitude": True, "longitude": False}, {"not_found": True},
     {"not_found": False, "reason": "x"}, {"not_found": True, "reason": 5}],
)
def test_damaged_entries_are_ignored_and_replaced(cache, junk):
    cache.set(KEY, junk, ttl=3600)
    inner = ScriptedGeocoder()
    geocoder = make(inner, cache)
    assert geocoder.geocode(ADDRESS) == FOUND
    assert len(inner.calls) == 1
    assert geocoder.geocode(ADDRESS) == FOUND and len(inner.calls) == 1  # now cached properly


def test_a_broken_database_falls_back_to_the_geocoder_every_time():
    inner = ScriptedGeocoder()
    geocoder = make(inner, SqliteCache(BrokenDatabase()))
    assert geocoder.geocode(ADDRESS) == FOUND and geocoder.geocode(ADDRESS) == FOUND
    assert len(inner.calls) == 2


# --- the default cache, logging, settings -----------------------------------------------------------------------------------

def test_the_default_cache_is_the_persistent_one_and_is_shared():
    first_inner, second_inner = ScriptedGeocoder(), ScriptedGeocoder()
    first = CachingGeocoder(first_inner, "census", GeocodeCacheSettings())
    first.geocode(ADDRESS)
    assert isinstance(first._cache, SqliteCache)  # conftest points it at a throwaway database
    CachingGeocoder(second_inner, "census", GeocodeCacheSettings()).geocode(ADDRESS)
    assert second_inner.calls == []  # a second object sees the first one's entry


def test_cache_hits_are_logged_without_the_address(cache, caplog):
    geocoder = make(ScriptedGeocoder(), cache)
    geocoder.geocode("77 Secret Ln, Hidden, CA 90000")
    with caplog.at_level(logging.INFO, logger="app.services.geocoding.caching"):
        geocoder.geocode("77 Secret Ln, Hidden, CA 90000")
    assert "geocode result=match cache=hit" in caplog.text
    assert "Secret" not in caplog.text and "Hidden" not in caplog.text


def test_cached_not_found_is_logged(cache, caplog):
    geocoder = make(ScriptedGeocoder(AddressNotFoundError(ADDRESS)), cache)
    with pytest.raises(AddressNotFoundError):
        geocoder.geocode(ADDRESS)
    with caplog.at_level(logging.INFO, logger="app.services.geocoding.caching"), pytest.raises(AddressNotFoundError):
        geocoder.geocode(ADDRESS)
    assert "geocode result=no_match cache=hit" in caplog.text


def test_default_cache_settings():
    s = GeocodeCacheSettings()
    assert (s.ttl_seconds, s.not_found_ttl_seconds, s.enabled) == (90 * DAY, DAY, True)
    assert not GeocodeCacheSettings(ttl_seconds=0).enabled


def test_cache_settings_from_env(monkeypatch):
    assert GeocodeCacheSettings.from_env({}) == GeocodeCacheSettings()
    s = GeocodeCacheSettings.from_env({"GEOCODE_CACHE_TTL_SECONDS": "3600", "GEOCODE_NOT_FOUND_TTL_SECONDS": "0"})
    assert (s.ttl_seconds, s.not_found_ttl_seconds) == (3600.0, 0.0)


@pytest.mark.parametrize(
    "name, value",
    [("GEOCODE_CACHE_TTL_SECONDS", "-1"), ("GEOCODE_CACHE_TTL_SECONDS", "abc"), ("GEOCODE_CACHE_TTL_SECONDS", str(366 * DAY)),
     ("GEOCODE_NOT_FOUND_TTL_SECONDS", "-1"), ("GEOCODE_NOT_FOUND_TTL_SECONDS", str(31 * DAY))],
)
def test_invalid_cache_settings_are_refused(name, value):
    from app.services.geocoding import GeocoderConfigurationError

    with pytest.raises(GeocoderConfigurationError, match=name):
        GeocodeCacheSettings.from_env({name: value})
