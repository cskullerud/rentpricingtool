import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import APP_NAME, VERSION
from app.persistence import PersistenceError, get_database_manager
from app.routers import history, stats, valuation
from app.routers import ui as ui_router
from app.ui.errors import is_ui_page_request, render_error
from app.ui.ingress import IngressMiddleware
from app.ui.templating import STATIC_DIR, STATIC_URL
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


# The handlers below answer UI pages with an HTML error page and everything else exactly as
# before (JSON). Only paths under /ui (not /ui/static) get HTML.
@app.exception_handler(DataSourceError)
async def data_source_error_handler(request: Request, exc: DataSourceError):
    logger.warning("Data source error on %s: %s", request.url.path, exc)
    if is_ui_page_request(request):
        return render_error(request, exc.status_code, "Data source problem", exc.public_message)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.public_message})


@app.exception_handler(ProviderConfigurationError)
async def provider_configuration_error_handler(request: Request, exc: ProviderConfigurationError):
    logger.error("Provider configuration error on %s: %s", request.url.path, exc)
    if is_ui_page_request(request):
        return render_error(
            request, 503, "Data source not set up",
            "The data source is not set up correctly. Please contact the administrator.",
        )
    return JSONResponse(
        status_code=503, content={"detail": "The comparable data provider is not configured correctly"}
    )


app.include_router(valuation.router)
app.include_router(stats.router)
app.include_router(history.router)
app.include_router(ui_router.router)
app.mount(STATIC_URL, StaticFiles(directory=STATIC_DIR), name="ui-static")
app.add_middleware(IngressMiddleware)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler_with_ui_pages(request: Request, exc: StarletteHTTPException):
    if is_ui_page_request(request):
        return render_error(request, exc.status_code)
    return await http_exception_handler(request, exc)  # FastAPI's default JSON response


@app.get("/")
def root() -> dict:
    return {"app": APP_NAME, "status": "online"}
