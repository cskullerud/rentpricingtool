"""SQLite connection management and schema, using only the standard library."""
import sqlite3
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from app.config import DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS valuation_requests (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,

    address TEXT,
    beds INTEGER,
    baths REAL,
    sqft INTEGER,

    latitude REAL,
    longitude REAL,

    comparable_count INTEGER,

    p25 REAL,
    median REAL,
    p75 REAL,
    average REAL,

    recommended_rent REAL
);

-- Responses from paid data providers (RentCast), so they survive restarts. Rows expire at
-- expires_at (epoch seconds) and are removed lazily.
CREATE TABLE IF NOT EXISTS provider_cache (
    cache_key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_cache_expires_at ON provider_cache (expires_at);
"""


class PersistenceError(Exception):
    """The database could not be opened, read or written."""


class DatabaseManager:
    """Opens connections to one SQLite file and makes sure the schema exists.

    Each `connect()` returns a new connection, because sqlite3 connections must not be
    shared between threads and FastAPI runs sync endpoints in a thread pool.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._initialized = False
        self._lock = threading.Lock()

    def initialize(self) -> None:
        """Create the database file, its folder and the schema if they don't exist yet."""
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self.path)
                try:
                    conn.executescript(SCHEMA)
                    conn.commit()
                finally:
                    conn.close()
            except (OSError, sqlite3.Error) as exc:
                raise PersistenceError(f"Could not initialize database at {self.path}: {exc}") from exc
            self._initialized = True

    def connect(self) -> sqlite3.Connection:
        """Return a new connection (rows behave like dicts). The caller must close it."""
        if not self._initialized:
            self.initialize()
        try:
            conn = sqlite3.connect(self.path)
        except sqlite3.Error as exc:
            raise PersistenceError(f"Could not open database at {self.path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits on success, rolls back on error, and always closes."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0


@lru_cache(maxsize=1)
def get_database_manager() -> DatabaseManager:
    """The application's database (one per process), at DATABASE_PATH."""
    return DatabaseManager(DATABASE_PATH)
