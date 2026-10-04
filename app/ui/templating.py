"""Jinja2 setup shared by the UI routes."""
from pathlib import Path

from jinja2 import pass_context
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates

from app.config import APP_NAME, VERSION
from app.services.data_sources import ProviderConfigurationError, get_provider_type
from app.ui import viewmodels

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

UI_PREFIX = "/ui"
STATIC_URL = f"{UI_PREFIX}/static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))  # autoescape is on for .html


def prefixed(request: Request, path: str) -> str:
    """`path` with the ingress prefix (if any) in front. All UI URLs go through this."""
    return f"{request.scope.get('ingress_prefix', '')}{path}"


@pass_context
def ui_url(context, path: str) -> str:
    """Template helper: {{ ui_url('/ui/history') }} -> '/api/hassio_ingress/<token>/ui/history'."""
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
    return {
        "label": label, "css": "text-bg-success", "title": f"Comparables come from {label}",
        "live": label == "RENTCAST",
    }


templates.env.globals.update(ui_url=ui_url, STATIC_URL=STATIC_URL, UI_PREFIX=UI_PREFIX, APP_NAME=APP_NAME, VERSION=VERSION)
templates.env.filters["money"] = viewmodels.money


def render(request: Request, template: str, active: str, status_code: int = 200, **context) -> HTMLResponse:
    """Render a page with the shared context (navbar state, data-source badge).

    Pages are marked no-store: they can contain a CSRF token and valuation results.
    """
    response = templates.TemplateResponse(
        request, template, {"active": active, "badge": data_source_badge(), **context}, status_code=status_code
    )
    response.headers["Cache-Control"] = "no-store"
    return response
