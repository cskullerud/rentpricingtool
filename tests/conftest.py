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
