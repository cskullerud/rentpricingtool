"""HTML pages. Everything here is under /ui and is hidden from the OpenAPI schema."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.ui.templating import data_source_badge, templates

router = APIRouter(include_in_schema=False)


def render(request: Request, template: str, active: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request, template, {"active": active, "badge": data_source_badge(), **context}
    )


# Both spellings are served directly. A redirect from /ui to /ui/ would have to rebuild the
# URL, which is fragile behind the ingress proxy.
@router.get("/ui", response_class=HTMLResponse)
@router.get("/ui/", response_class=HTMLResponse)
def valuation_form(request: Request) -> HTMLResponse:
    return render(request, "valuation_form.html", active="valuation")


@router.get("/ui/history", response_class=HTMLResponse)
def history(request: Request) -> HTMLResponse:
    return render(request, "history.html", active="history")
