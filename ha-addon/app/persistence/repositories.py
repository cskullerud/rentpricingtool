"""All SQL for valuation history lives here, so the rest of the app never sees it."""
import sqlite3
from datetime import datetime, timezone

from fastapi import Depends

from app.persistence.database import DatabaseManager, PersistenceError, get_database_manager
from app.schemas import ValuationRequest


class ValuationRepository:
    def __init__(self, database: DatabaseManager):
        self._database = database

    @property
    def database_path(self) -> str:
        return str(self._database.path)

    def database_size_bytes(self) -> int:
        return self._database.size_bytes()

    def save_valuation_request(
        self, subject: ValuationRequest, result: dict, created_at: str | None = None
    ) -> int:
        """Store one request and its result. Returns the new row's id.

        `created_at` defaults to now (UTC, ISO 8601). Latitude/longitude are stored as the
        request gave them, so they are NULL when the request relied on the default point.
        """
        created_at = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with self._database.connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO valuation_requests (
                        created_at, address, beds, baths, sqft, latitude, longitude,
                        comparable_count, p25, median, p75, average, recommended_rent
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created_at,
                        subject.address,
                        subject.beds,
                        subject.baths,
                        subject.sqft,
                        subject.latitude,
                        subject.longitude,
                        result["comparable_count"],
                        result["p25"],
                        result["median"],
                        result["p75"],
                        result["average"],
                        result["recommended_rent"],
                    ),
                )
                return cursor.lastrowid
        except sqlite3.Error as exc:
            raise PersistenceError(f"Could not save valuation: {exc}") from exc

    def get_recent_valuations(self, limit: int = 25) -> list[dict]:
        """The most recent valuations, newest first."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        try:
            with self._database.connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM valuation_requests ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"Could not read valuations: {exc}") from exc
        return [dict(row) for row in rows]

    def count_valuations(self) -> int:
        try:
            with self._database.connection() as conn:
                return conn.execute("SELECT COUNT(*) FROM valuation_requests").fetchone()[0]
        except sqlite3.Error as exc:
            raise PersistenceError(f"Could not count valuations: {exc}") from exc


def get_repository(
    database: DatabaseManager = Depends(get_database_manager),
) -> ValuationRepository:
    """FastAPI dependency. Tests override this to use a temporary database."""
    return ValuationRepository(database)
