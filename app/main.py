import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import APP_NAME, VERSION
from app.persistence import PersistenceError, get_database_manager
from app.routers import history, stats, valuation
from app.services.data_sources import (
    DataSourceError,
    ProviderConfigurationError,
    get_provider,
    get_provider_type,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the SQLite file and schema at startup. If that fails, keep serving:
    # valuations still work, and /stats and /history report the database as unavailable.
    try:
        get_database_manager().initialize()
    except PersistenceError:
        logger.exception("Could not initialize the database")
    # Report the comparable provider once at startup. A bad setting is logged loudly but does
    # not stop the app: /history and /stats don't need a provider, and /valuation answers 503.
    try:
        provider = get_provider_type()
        get_provider()  # builds the source, which validates its settings; makes no API call
        logger.info("Comparable data provider: %s", provider.value)
    except ProviderConfigurationError:
        logger.exception("Comparable data provider is misconfigured; /valuation will fail")
    yield


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan)


@app.exception_handler(DataSourceError)
async def data_source_error_handler(request: Request, exc: DataSourceError) -> JSONResponse:
    logger.warning("Data source error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.public_message})


@app.exception_handler(ProviderConfigurationError)
async def provider_configuration_error_handler(request: Request, exc: ProviderConfigurationError) -> JSONResponse:
    logger.error("Provider configuration error on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503, content={"detail": "The comparable data provider is not configured correctly"}
    )


app.include_router(valuation.router)
app.include_router(stats.router)
app.include_router(history.router)


@app.get("/")
def root() -> dict:
    return {"app": APP_NAME, "status": "online"}
