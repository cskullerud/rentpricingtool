"""HTML error pages for the UI. They show a short plain message and never any technical detail."""
import logging

from starlette.requests import Request
from starlette.responses import HTMLResponse

from app.ui.templating import STATIC_URL, UI_PREFIX, render

logger = logging.getLogger(__name__)

TITLES = {
    404: ("Page not found", "That page doesn't exist."),
    405: ("Not allowed", "That page can't be used this way."),
}
DEFAULT = ("Something went wrong", "Something went wrong. Please try again.")


def is_ui_page_request(request: Request) -> bool:
    """True for requests to UI pages (not the static files, not the JSON API)."""
    path = request.scope.get("path", "")
    root = request.scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root):]
    if path == STATIC_URL or path.startswith(STATIC_URL + "/"):
        return False
    return path == UI_PREFIX or path.startswith(UI_PREFIX + "/")


def render_error(request: Request, status_code: int, title: str | None = None, message: str | None = None) -> HTMLResponse:
    default_title, default_message = TITLES.get(status_code, DEFAULT)
    return render(
        request, "error.html", active="",
        status_code=status_code, error_title=title or default_title, error_message=message or default_message,
    )
