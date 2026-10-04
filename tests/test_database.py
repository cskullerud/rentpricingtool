import sqlite3

import pytest

from app.config import DATABASE_PATH, PROJECT_ROOT
from app.persistence import DatabaseManager, PersistenceError

EXPECTED_COLUMNS = [
    "id", "created_at", "address", "beds", "baths", "sqft", "latitude", "longitude",
    "comparable_count", "p25", "median", "p75", "average", "recommended_rent",
]


def test_default_location_is_data_rentpricingtool_db():
    assert DATABASE_PATH == str(PROJECT_ROOT / "data" / "rentpricingtool.db")


def test_creates_the_file_and_missing_folders_automatically(tmp_path):
    path = tmp_path / "nested" / "dir" / "app.db"
    assert not path.exists()
    DatabaseManager(path).initialize()
    assert path.is_file()


def test_connect_creates_the_database_on_first_use(tmp_path):
    manager = DatabaseManager(tmp_path / "lazy.db")
    conn = manager.connect()
    conn.close()
    assert (tmp_path / "lazy.db").is_file()


def test_schema_has_the_valuation_requests_table(tmp_path):
    manager = DatabaseManager(tmp_path / "schema.db")
    with manager.connection() as conn:
        info = conn.execute("PRAGMA table_info(valuation_requests)").fetchall()
    assert [row["name"] for row in info] == EXPECTED_COLUMNS
    types = {row["name"]: row["type"] for row in info}
    assert types["id"] == "INTEGER" and types["created_at"] == "TEXT"
    assert types["baths"] == "REAL" and types["median"] == "REAL" and types["beds"] == "INTEGER"
    assert {row["name"] for row in info if row["pk"]} == {"id"}
    assert {row["name"] for row in info if row["notnull"]} == {"created_at"}


def test_initialize_is_idempotent_and_keeps_data(tmp_path):
    manager = DatabaseManager(tmp_path / "again.db")
    with manager.connection() as conn:
        conn.execute("INSERT INTO valuation_requests (created_at) VALUES ('2026-01-01T00:00:00')")
    manager.initialize()
    manager.initialize()
    with manager.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM valuation_requests").fetchone()[0] == 1


def test_connection_commits_on_success_and_rolls_back_on_error(tmp_path):
    manager = DatabaseManager(tmp_path / "tx.db")
    with manager.connection() as conn:
        conn.execute("INSERT INTO valuation_requests (created_at) VALUES ('kept')")
    with pytest.raises(RuntimeError):
        with manager.connection() as conn:
            conn.execute("INSERT INTO valuation_requests (created_at) VALUES ('discarded')")
            raise RuntimeError("boom")
    with manager.connection() as conn:
        rows = [r["created_at"] for r in conn.execute("SELECT created_at FROM valuation_requests")]
    assert rows == ["kept"]


def test_created_at_is_required(tmp_path):
    manager = DatabaseManager(tmp_path / "notnull.db")
    with pytest.raises(sqlite3.IntegrityError):
        with manager.connection() as conn:
            conn.execute("INSERT INTO valuation_requests (address) VALUES ('x')")


def test_size_bytes_is_zero_before_creation_and_positive_after(tmp_path):
    manager = DatabaseManager(tmp_path / "size.db")
    assert manager.size_bytes() == 0
    manager.initialize()
    assert manager.size_bytes() > 0


def test_unusable_location_raises_persistence_error(tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("not a folder")
    with pytest.raises(PersistenceError):
        DatabaseManager(blocker / "db.sqlite").initialize()
