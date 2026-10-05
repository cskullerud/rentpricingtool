import json
import sqlite3

import pytest

from app.persistence import DatabaseManager, PersistenceError, SqliteCache
from app.services.data_sources import RentCastComparableSource, RentCastSettings, SubjectProperty
from app.services.data_sources import rentcast_source

SUBJECT = SubjectProperty("1 A St", 32.7678, -117.0231, 3, 2.0, 1400)
LISTING = {
    "formattedAddress": "10 Oak St, La Mesa, CA", "price": 2450, "latitude": 32.77,
    "longitude": -117.02, "bedrooms": 3, "bathrooms": 2, "squareFootage": 1350,
}


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class CountingTransport:
    def __init__(self, listings=None):
        self.body = json.dumps([LISTING] if listings is None else listings).encode()
        self.calls = 0

    def __call__(self, url, headers, timeout):
        self.calls += 1
        return 200, self.body


@pytest.fixture
def database(tmp_path):
    return DatabaseManager(tmp_path / "cache_test.db")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(database, clock):
    return SqliteCache(database, clock=clock)


# --- the cache itself ------------------------------------------------------------------

def test_miss_on_an_empty_cache(cache):
    assert cache.get(("k", 1)) is None


def test_hit_returns_what_was_stored(cache):
    value = [{"address": "a", "rent": 2000, "beds": 2, "baths": 1.5}]
    cache.set(("k", 1.5), value, ttl=60)
    assert cache.get(("k", 1.5)) == value


def test_different_keys_do_not_collide(cache):
    cache.set(("a", 1), [1], ttl=60)
    cache.set(("a", 2), [2], ttl=60)
    assert (cache.get(("a", 1)), cache.get(("a", 2)), cache.get(("a", 3))) == ([1], [2], None)


def test_an_empty_list_is_a_hit_not_a_miss(cache):
    cache.set("k", [], ttl=60)
    assert cache.get("k") == []


def test_expiry_boundary_matches_the_in_memory_cache(cache, clock):
    cache.set("k", [1], ttl=60)
    clock.now += 59.9
    assert cache.get("k") == [1]
    clock.now += 0.1  # now - written == ttl exactly: expired, as with TtlCache (>=)
    assert cache.get("k") is None


def test_expired_rows_are_removed(cache, database, clock):
    cache.set("old", [1], ttl=10)
    clock.now += 11
    assert cache.get("old") is None
    with database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM provider_cache").fetchone()[0] == 0


def test_set_purges_other_expired_rows(cache, database, clock):
    cache.set("old", [1], ttl=10)
    clock.now += 11
    cache.set("new", [2], ttl=10)
    with database.connection() as conn:
        keys = [r[0] for r in conn.execute("SELECT cache_key FROM provider_cache")]
    assert keys == ['"new"']


def test_set_overwrites_and_restarts_the_ttl(cache, clock):
    cache.set("k", [1], ttl=10)
    clock.now += 8
    cache.set("k", [2], ttl=10)
    clock.now += 8
    assert cache.get("k") == [2]


def test_clear_removes_everything(cache):
    cache.set("a", [1], ttl=60)
    cache.set("b", [2], ttl=60)
    cache.clear()
    assert cache.get("a") is None and cache.get("b") is None


def test_entries_survive_a_new_cache_object_and_database_manager(tmp_path, clock):
    """The point of the feature: a restart builds new objects over the same file."""
    path = tmp_path / "persist.db"
    SqliteCache(DatabaseManager(path), clock=clock).set(("k",), [{"rent": 1}], ttl=60)
    assert SqliteCache(DatabaseManager(path), clock=clock).get(("k",)) == [{"rent": 1}]


def test_entries_expire_across_a_restart(tmp_path, clock):
    path = tmp_path / "persist.db"
    SqliteCache(DatabaseManager(path), clock=clock).set("k", [1], ttl=60)
    clock.now += 61
    assert SqliteCache(DatabaseManager(path), clock=clock).get("k") is None


def test_the_schema_adds_the_table_without_touching_valuation_requests(database):
    with database.connection() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = [r[1] for r in conn.execute("PRAGMA table_info(provider_cache)")]
    assert {"valuation_requests", "provider_cache"} <= tables
    assert columns == ["cache_key", "value", "created_at", "expires_at"]


def test_an_existing_database_gains_the_table_on_initialize(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE valuation_requests (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO valuation_requests (created_at) VALUES ('2026-01-01')")
    conn.commit()
    conn.close()
    DatabaseManager(path).initialize()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM provider_cache").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM valuation_requests").fetchone()[0] == 1
    conn.close()


# --- failures never break callers ------------------------------------------------------

class BrokenDatabase:
    def connection(self):
        raise PersistenceError("database is down")


def test_a_broken_database_reads_as_a_miss_and_writes_are_ignored():
    cache = SqliteCache(BrokenDatabase())
    assert cache.get("k") is None
    cache.set("k", [1], ttl=60)  # must not raise
    cache.clear()


def test_a_corrupt_stored_value_reads_as_a_miss(cache, database):
    cache.set("k", [1], ttl=60)
    with database.connection() as conn:
        conn.execute("UPDATE provider_cache SET value = 'not json'")
    assert cache.get("k") is None


# --- RentCast source on top of the persistent cache ------------------------------------

def make_source(transport, cache, **settings):
    return RentCastComparableSource(
        RentCastSettings(api_key="k", **settings), transport=transport, cache=cache, sleep=lambda s: None
    )


def test_second_valuation_is_a_cache_hit(cache):
    transport = CountingTransport()
    source = make_source(transport, cache)
    first = source.get_comparables(SUBJECT)
    assert source.get_comparables(SUBJECT) == first
    assert transport.calls == 1


def test_a_hit_survives_a_restart_so_no_api_call_is_made(tmp_path, clock):
    path = tmp_path / "restart.db"
    transport = CountingTransport()
    first = make_source(transport, SqliteCache(DatabaseManager(path), clock=clock)).get_comparables(SUBJECT)
    # "restart": brand-new source, cache and database manager over the same file
    again = make_source(transport, SqliteCache(DatabaseManager(path), clock=clock)).get_comparables(SUBJECT)
    assert again == first and again
    assert transport.calls == 1


def test_a_different_area_is_a_miss(cache):
    transport = CountingTransport()
    source = make_source(transport, cache)
    source.get_comparables(SUBJECT)
    source.get_comparables(SubjectProperty("far", 33.5, -117.5, 3, 2, 1400))
    assert transport.calls == 2


def test_nearby_locations_share_an_entry(cache):
    transport = CountingTransport()
    source = make_source(transport, cache)
    source.get_comparables(SUBJECT)
    source.get_comparables(SubjectProperty("next door", 32.76781, -117.02309, 2, 1, 900))
    assert transport.calls == 1


def test_a_different_radius_or_limit_is_a_miss(cache):
    transport = CountingTransport()
    from dataclasses import replace

    make_source(transport, cache).get_comparables(replace(SUBJECT, search_radius_miles=5.0))
    make_source(transport, cache).get_comparables(replace(SUBJECT, search_radius_miles=2.0))
    make_source(transport, cache, limit=50).get_comparables(replace(SUBJECT, search_radius_miles=5.0))
    assert transport.calls == 3


def test_an_expired_entry_triggers_a_new_api_call(cache, clock):
    transport = CountingTransport()
    source = make_source(transport, cache, cache_ttl_seconds=3600)
    source.get_comparables(SUBJECT)
    clock.now += 3599
    source.get_comparables(SUBJECT)
    assert transport.calls == 1
    clock.now += 2
    source.get_comparables(SUBJECT)
    assert transport.calls == 2


def test_ttl_zero_never_writes_to_the_cache(cache, database):
    transport = CountingTransport()
    source = make_source(transport, cache, cache_ttl_seconds=0)
    source.get_comparables(SUBJECT)
    source.get_comparables(SUBJECT)
    assert transport.calls == 2
    with database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM provider_cache").fetchone()[0] == 0


def test_an_empty_result_is_cached_too(cache):
    transport = CountingTransport(listings=[])
    source = make_source(transport, cache)
    assert source.get_comparables(SUBJECT) == []
    assert source.get_comparables(SUBJECT) == []
    assert transport.calls == 1


def test_failed_calls_are_not_cached(cache):
    class Failing:
        calls = 0

        def __call__(self, url, headers, timeout):
            self.calls += 1
            return 503, b""

    failing = Failing()
    with pytest.raises(Exception):
        make_source(failing, cache, max_retries=0).get_comparables(SUBJECT)
    working = CountingTransport()
    assert make_source(working, cache).get_comparables(SUBJECT)
    assert working.calls == 1


def test_callers_cannot_modify_cached_data(cache):
    source = make_source(CountingTransport(), cache)
    source.get_comparables(SUBJECT)[0]["rent"] = -1
    assert source.get_comparables(SUBJECT)[0]["rent"] == 2450


def test_the_cache_key_and_stored_rows_contain_no_api_key(cache, database):
    source = RentCastComparableSource(
        RentCastSettings(api_key="SECRET-KEY-123"), transport=CountingTransport(), cache=cache
    )
    source.get_comparables(SUBJECT)
    with database.connection() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT * FROM provider_cache")]
    assert rows and "SECRET-KEY-123" not in json.dumps(rows)


def test_a_database_failure_falls_back_to_calling_the_api():
    transport = CountingTransport()
    source = make_source(transport, SqliteCache(BrokenDatabase()))
    assert source.get_comparables(SUBJECT)
    assert source.get_comparables(SUBJECT)
    assert transport.calls == 2


def test_the_default_cache_is_the_persistent_one(monkeypatch):
    # conftest points _default_cache at a temp database; the source must use it
    transport = CountingTransport()
    source = RentCastComparableSource(RentCastSettings(api_key="k"), transport=transport)
    source.get_comparables(SUBJECT)
    assert isinstance(source._cache, SqliteCache)
    other = RentCastComparableSource(RentCastSettings(api_key="k"), transport=transport)
    other.get_comparables(SUBJECT)
    assert transport.calls == 1  # a second source object sees the first one's entry


def test_there_is_no_module_level_in_memory_cache_any_more():
    assert not hasattr(rentcast_source, "_SHARED_CACHE")
