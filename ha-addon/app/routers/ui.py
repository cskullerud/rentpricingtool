"""HTML pages. Everything here is under /ui and is hidden from the OpenAPI schema.

The valuation page reuses the API's engine and dependencies (get_comparable_source,
get_geocoder, get_repository), so tests override them exactly as they do for the API.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app.config import MIN_COMPARABLES_REQUIRED
from app.persistence.repositories import ValuationRepository, get_repository
from app.routers.valuation import get_comparable_source, get_geocoder
from app.schemas import ValuationResponse
from app.services.data_sources import ComparableDataSource, DataSourceError, ProviderConfigurationError
from app.services.geocoding import AddressNotFoundError, Geocoder
from app.services.valuation_engine import InsufficientDataError, run_valuation
from app.ui import csrf, viewmodels
from app.ui.forms import ValuationForm
from app.services.search_options import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PROPERTY_TYPE,
    DEFAULT_RADIUS_MILES,
    LOOKBACK_OPTIONS_DAYS,
    PROPERTY_TYPES,
    RADIUS_OPTIONS_MILES,
)
from app.ui.templating import UI_PREFIX, data_source_badge, geocoder_info, prefixed, render

logger = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)


def summary_rows(form: ValuationForm) -> list[dict]:
    """The Search criteria panel for what the form currently holds. An invalid select (a tampered
    post) shows the default instead, so the panel never displays something the search would not use."""
    values = form.values
    try:
        radius = float(values["search_radius_miles"])
    except ValueError:
        radius = DEFAULT_RADIUS_MILES
    lookback = int(values["lookback_days"]) if values["lookback_days"].isdigit() else DEFAULT_LOOKBACK_DAYS
    property_type = values["property_type"] if values["property_type"] in PROPERTY_TYPES else DEFAULT_PROPERTY_TYPE
    return viewmodels.search_summary(
        address=values["address"],
        radius_miles=radius if radius in RADIUS_OPTIONS_MILES else DEFAULT_RADIUS_MILES,
        lookback_days=lookback if lookback in LOOKBACK_OPTIONS_DAYS else DEFAULT_LOOKBACK_DAYS,
        property_type=property_type,
        sqft_given=bool(values["sqft"].strip()),
        minimum_comparables=MIN_COMPARABLES_REQUIRED,
        data_source=data_source_badge()["label"],
        geocoder=geocoder_info()["label"],
    )


def render_form(request: Request, form: ValuationForm, status_code: int = 200, **context) -> HTMLResponse:
    """The valuation page, with a fresh CSRF token (and cookie, if the visitor has none)."""
    cookie_value = request.cookies.get(csrf.COOKIE_NAME)
    is_new = not csrf.is_well_formed(cookie_value)
    if is_new:
        cookie_value = csrf.new_cookie_value()
    response = render(
        request, "valuation_form.html", active="valuation", status_code=status_code,
        form=form, csrf_token=csrf.token_for(cookie_value), summary_rows=summary_rows(form), **context,
    )
    if is_new:
        forwarded = request.headers.get("x-forwarded-proto", request.url.scheme)
        response.set_cookie(
            csrf.COOKIE_NAME, cookie_value, max_age=csrf.COOKIE_MAX_AGE,
            path=prefixed(request, UI_PREFIX), httponly=True, samesite="lax", secure=forwarded == "https",
        )
    return response


def alert(message: str, level: str = "danger") -> dict:
    return {"level": level, "message": message}


# Both spellings are served directly. A redirect from /ui to /ui/ would have to rebuild the
# URL, which is fragile behind the ingress proxy.
@router.get("/ui", response_class=HTMLResponse)
@router.get("/ui/", response_class=HTMLResponse)
def valuation_form(request: Request) -> HTMLResponse:
    return render_form(request, ValuationForm.blank())


@router.post("/ui/valuation", response_class=HTMLResponse)
async def submit_valuation(
    request: Request,
    source: ComparableDataSource = Depends(get_comparable_source),
    repository: ValuationRepository = Depends(get_repository),
    geocoder: Geocoder = Depends(get_geocoder),
) -> HTMLResponse:
    data = await request.form()
    form = ValuationForm.from_form(data)

    token = data.get(csrf.FORM_FIELD)
    if not csrf.verify(request.cookies.get(csrf.COOKIE_NAME), token if isinstance(token, str) else None):
        form.errors = {}  # the entries were not checked, so show only the "submit again" message
        return render_form(
            request, form, status_code=403,
            alert=alert("This form expired or could not be verified. Your entries are kept; please submit again."),
        )
    if not form.is_valid:
        return render_form(request, form, status_code=422)

    try:
        # Same engine call as POST /valuation; run off the event loop because it is blocking.
        result = await run_in_threadpool(run_valuation, form.request, source, repository, geocoder)
    except InsufficientDataError as exc:
        body = exc.to_response()
        funnel, search = body.get("funnel"), body.get("search")
        return render_form(
            request, form,
            insufficient={
                "comparable_count": exc.comparable_count,
                "minimum_required": exc.minimum_required,
                "detail": body["detail"],
                "funnel_rows": (
                    viewmodels.funnel_rows(
                        funnel,
                        search["radius_miles"] if search else None,
                        search["lookback_days"] if search else None,
                        search["sqft_used"] if search else True,
                    )
                    if funnel else []
                ),
                "explanation": viewmodels.explain_insufficient(funnel, exc.minimum_required, search) if funnel else "",
                "search_line": viewmodels.search_line(search) if search else "",
            },
        )
    except AddressNotFoundError as exc:
        reason = exc.reason[:1].upper() + exc.reason[1:]
        form.errors["address"] = f"{reason}. Or enter exact coordinates below."
        form.expand_coordinates = True
        return render_form(request, form, status_code=422)
    except DataSourceError as exc:
        logger.warning("UI valuation failed: %s", exc)
        return render_form(request, form, status_code=exc.status_code, alert=alert(exc.public_message))
    except ProviderConfigurationError as exc:
        logger.error("UI valuation failed, provider misconfigured: %s", exc)
        return render_form(
            request, form, status_code=503, alert=alert("The data source is not set up correctly. Please contact the administrator.")
        )
    except Exception:
        logger.exception("Unexpected error during a UI valuation")
        return render_form(request, form, status_code=500, alert=alert("Something went wrong. Please try again."))

    response = ValuationResponse(**result).model_dump()
    search = response["search"]
    return render_form(
        request, form,
        result=response,
        confidence=viewmodels.confidence_style(response["confidence"]),
        funnel_rows=viewmodels.funnel_rows(
            response["funnel"], search["radius_miles"], search["lookback_days"], search["sqft_used"]
        ),
        search_line=viewmodels.search_line(search),
    )


@router.get("/ui/history", response_class=HTMLResponse)
def history(request: Request) -> HTMLResponse:
    return render(request, "history.html", active="history")
