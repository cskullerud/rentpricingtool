import pytest

from app.main import app
from app.persistence import DatabaseManager, ValuationRepository, get_repository


@pytest.fixture
def repository(tmp_path):
    """A repository backed by a throwaway database file."""
    return ValuationRepository(DatabaseManager(tmp_path / "test.db"))


@pytest.fixture(autouse=True)
def isolated_database(repository):
    """Every test gets its own temporary database, so no test touches data/rentpricingtool.db."""
    app.dependency_overrides[get_repository] = lambda: repository
    yield
    app.dependency_overrides.pop(get_repository, None)


@pytest.fixture(autouse=True)
def isolated_provider_cache(tmp_path, monkeypatch):
    """The RentCast cache defaults to the app database; point it at a throwaway file."""
    from app.persistence import DatabaseManager, SqliteCache
    from app.services.data_sources import rentcast_source

    cache_db = DatabaseManager(tmp_path / "provider_cache.db")
    monkeypatch.setattr(rentcast_source, "_default_cache", lambda: SqliteCache(cache_db))
