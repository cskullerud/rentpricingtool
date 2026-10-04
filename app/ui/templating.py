"""Jinja2 setup shared by the UI routes."""
from pathlib import Path

from jinja2 import pass_context
from starlette.requests import Request
from starlette.templating import Jinja2Templates

from app.config import APP_NAME, VERSION
from app.services.data_sources import ProviderConfigurationError, get_provider_type

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

UI_PREFIX = "/ui"
STATIC_URL = f"{UI_PREFIX}/static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))  # autoescape is on for .html


def prefixed(request: Request, path: str) -> str:
    """`path` with the ingress prefix (if any) in front. All UI URLs go through this."""
    return f"{request.scope.get('root_path', '')}{path}"


@pass_context
def u(context, path: str) -> str:
    """Template helper: {{ u('/ui/history') }} -> '/api/hassio_ingress/<token>/ui/history'."""
    return prefixed(context["request"], path)


def data_source_badge() -> dict:
    """What the navbar badge shows: MOCK or RENTCAST (or CSV), or MISCONFIGURED."""
    try:
        provider = get_provider_type()
    except ProviderConfigurationError:
        return {"label": "MISCONFIGURED", "css": "text-bg-danger", "title": "DATA_PROVIDER has an unknown value"}
    label = provider.value.upper()
    if label == "MOCK":
        return {"label": label, "css": "text-bg-secondary", "title": "Built-in sample data, not real listings"}
    return {"label": label, "css": "text-bg-success", "title": f"Comparables come from {label}"}


templates.env.globals.update(u=u, STATIC_URL=STATIC_URL, UI_PREFIX=UI_PREFIX, APP_NAME=APP_NAME, VERSION=VERSION)
