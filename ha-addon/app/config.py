import os
from pathlib import Path

APP_NAME: str = os.getenv("APP_NAME", "Rent Pricing Tool")
VERSION: str = os.getenv("VERSION", "0.2.0")
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

# Mock-phase placeholder: when a valuation request has no coordinates, the subject is
# placed here (La Mesa, CA). Replaced by geocoding the address in a later phase.
DEFAULT_SUBJECT_LATITUDE: float = 32.7678
DEFAULT_SUBJECT_LONGITUDE: float = -117.0231

# A valuation needs at least this many comparables after filtering and outlier removal;
# with fewer, the engine reports insufficient data instead of a number. Read at import, like
# the other settings, so a bad value stops the app at startup rather than at a request.
DEFAULT_MIN_COMPARABLES_REQUIRED: int = 3


def _min_comparables_from_env() -> int:
    raw = os.getenv("MIN_COMPARABLES_REQUIRED", "").strip()
    if not raw:
        return DEFAULT_MIN_COMPARABLES_REQUIRED
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"MIN_COMPARABLES_REQUIRED must be a whole number, got {raw!r}") from None
    if value < 1:
        raise ValueError(f"MIN_COMPARABLES_REQUIRED must be at least 1, got {value}")
    return value


MIN_COMPARABLES_REQUIRED: int = _min_comparables_from_env()

# SQLite file for valuation history. Created automatically on first use.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATABASE_PATH: str = os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "rentpricingtool.db"))
