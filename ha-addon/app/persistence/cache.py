"""A persistent, expiring key-value cache in the application's SQLite database.

Used for provider responses that cost money to fetch (RentCast), so they survive restarts.
It is best effort: if the database is unavailable the cache behaves as empty and the caller
fetches normally, so a cache problem never fails a valuation.
"""
import json
import logging
import sqlite3
import time
from collections.abc import Callable

from app.persistence.database import DatabaseManager, PersistenceError

logger = logging.getLogger(__name__)

_ERRORS = (PersistenceError, sqlite3.Error, ValueError)


class SqliteCache:
    """get/set/clear with per-entry expiry. Values must be JSON-serializable.

    Expiry matches the in-memory cache: an entry is gone once `clock() >= expires_at`, where
    expires_at is the write time plus the TTL. The clock is wall-clock time because it has to
    mean the same thing after a restart.
    """

    def __init__(self, database: DatabaseManager, clock: Callable[[], float] = time.time):
        self._database = database
        self._clock = clock

    @staticmethod
    def _encode_key(key) -> str:
        return json.dumps(key, separators=(",", ":"))

    def get(self, key):
        """Return the cached value, or None on a miss, an expired entry or any database error."""
        encoded = self._encode_key(key)
        try:
            with self._database.connection() as conn:
                row = conn.execute(
                    "SELECT value, expires_at FROM provider_cache WHERE cache_key = ?", (encoded,)
                ).fetchone()
                if row is None:
                    return None
                if self._clock() >= row["expires_at"]:
                    conn.execute("DELETE FROM provider_cache WHERE cache_key = ?", (encoded,))
                    return None
                return json.loads(row["value"])
        except _ERRORS:
            logger.exception("Cache read failed; treating it as a miss")
            return None

    def set(self, key, value, ttl: float) -> None:
        """Store `value` for `ttl` seconds. Failures are logged, not raised."""
        now = self._clock()
        try:
            with self._database.connection() as conn:
                conn.execute("DELETE FROM provider_cache WHERE expires_at <= ?", (now,))
                conn.execute(
                    "INSERT OR REPLACE INTO provider_cache (cache_key, value, created_at, expires_at)"
                    " VALUES (?, ?, ?, ?)",
                    (self._encode_key(key), json.dumps(value), now, now + ttl),
                )
        except _ERRORS + (TypeError,):
            logger.exception("Cache write failed; continuing without caching")

    def clear(self) -> None:
        try:
            with self._database.connection() as conn:
                conn.execute("DELETE FROM provider_cache")
        except _ERRORS:
            logger.exception("Cache clear failed")
