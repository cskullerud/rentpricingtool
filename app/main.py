import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import APP_NAME, VERSION
from app.persistence import PersistenceError, get_database_manager
from app.routers import history, stats, valuation

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the SQLite file and schema at startup. If that fails, keep serving:
    # valuations still work, and /stats and /history report the database as unavailable.
    try:
        get_database_manager().initialize()
    except PersistenceError:
        logger.exception("Could not initialize the database")
    yield


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan)
app.include_router(valuation.router)
app.include_router(stats.router)
app.include_router(history.router)


@app.get("/")
def root() -> dict:
    return {"app": APP_NAME, "status": "online"}
