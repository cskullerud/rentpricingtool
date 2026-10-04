import os
from pathlib import Path

APP_NAME: str = os.getenv("APP_NAME", "Rent Pricing Tool")
VERSION: str = os.getenv("VERSION", "0.1.0")
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

# Mock-phase placeholder: when a valuation request has no coordinates, the subject is
# placed here (La Mesa, CA). Replaced by geocoding the address in a later phase.
DEFAULT_SUBJECT_LATITUDE: float = 32.7678
DEFAULT_SUBJECT_LONGITUDE: float = -117.0231

# SQLite file for valuation history. Created automatically on first use.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATABASE_PATH: str = os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "rentpricingtool.db"))
