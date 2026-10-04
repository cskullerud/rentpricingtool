import re
import urllib.request
from html import unescape

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SUBJECT_LATITUDE as LAT
from app.config import DEFAULT_SUBJECT_LONGITUDE as LON
from app.main import app
from app.routers.valuation import get_comparable_source
from app.services.data_sources import (
    ComparableDataSource,
    ProviderConfigurationError,
    RentCastAuthError,
    RentCastUnavailableError,
)
from app.ui import csrf

PREFIX = "/api/hassio_ingress/AbC123xyz"
FORM = {"address": "123 Main St", "beds": "3", "baths": "2", "sqft": "1400", "latitude": "", "longitude": ""}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """These tests use mock sources only. Any attempt to reach the network fails the test."""
    def blocked(*args, **kwargs):
        raise AssertionError("a UI test tried to use the network")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.delenv("DATA_PROVIDER", raising=False)


@pytest.fixture
def client():
    return TestClient(app)  # its own cookie jar, so each test starts without a CSRF cookie


def comp(rent, **overrides):
    base = {
        "address": f"{rent} Test St", "rent": rent, "latitude": LAT, "longitude": LON,
        "beds": 3, "baths": 2, "sqft": 1400,
    }
    base.update(overrides)
    return base


class ListSource(ComparableDataSource):
    """Returns exactly the comparables it is given, and counts calls. No provider, no network."""

    def __init__(self, comparables=()):
        self.comparables = list(comparables)
        self.calls = 0

    def get_comparables(self, subject=None):
        self.calls += 1
        return [dict(c) for c in self.comparables]


class FailingSource(ComparableDataSource):
    def __init__(self, error):
        self.error = error

    def get_comparables(self, subject=None):
        raise self.error


def matching(n):
    return [comp(2000 + 10 * i) for i in range(n)]


def use_source(source):
    app.dependency_overrides[get_comparable_source] = lambda: source


@pytest.fixture(autouse=True)
def reset_source_override():
    yield
    app.dependency_overrides.pop(get_comparable_source, None)


def token_of(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def submit(client, source=None, path="/ui/valuation", **fields):
    """Load the form (getting the cookie and token), then post it. Returns the POST response."""
    if source is not None:
        use_source(source)
    page = client.get("/ui/")
    return client.post(path, data={**FORM, "csrf_token": token_of(page), **fields})


def text_of(html, element_id):
    """The visible text of the element with this id (tags removed, entities decoded)."""
    match = re.search(rf'<(\w+)[^>]*id="{element_id}"[^>]*>(.*?)</\1>', html, re.S)
    if not match:
        return None
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", match.group(2)))).strip()


# --- the form page and its token ---------------------------------------------------------

def test_form_page_has_a_hidden_token_and_sets_the_csrf_cookie(client):
    response = client.get("/ui/")
    assert re.search(r'<input type="hidden" name="csrf_token" value="[0-9a-f]{64}">', response.text)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{csrf.COOKIE_NAME}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/ui" in cookie
    assert "Max-Age=28800" in cookie
    assert response.headers["cache-control"] == "no-store"


def test_the_cookie_is_reused_not_reissued(client):
    first = client.get("/ui/")
    second = client.get("/ui/")
    assert "set-cookie" not in second.headers
    assert token_of(first) == token_of(second)


def test_secure_flag_only_behind_https(client):
    assert "Secure" not in client.get("/ui/").headers["set-cookie"]
    other = TestClient(app)
    assert "Secure" in other.get("/ui/", headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"]


# --- successful submission ----------------------------------------------------------------

def test_successful_submission_renders_the_result(client):
    response = submit(client)
    assert response.status_code == 200
    html = response.text
    assert 'id="result-card"' in html
    assert text_of(html, "recommended-rent").startswith("$2,512")
    assert "Based on 16 comparable properties" in html
    assert 'id="insufficient-data"' not in html and 'id="page-alert"' not in html


def test_the_page_shows_the_same_numbers_as_the_api(client):
    api = client.post("/valuation", json={"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}).json()
    html = submit(client).text
    assert text_of(html, "recommended-rent").startswith(f"${api['recommended_rent']:,}")
    for key in ("p25", "median", "p75", "average"):
        assert f"${api[key]:,}" in html
    assert f"Based on {api['comparable_count']} comparable" in html


def test_a_successful_submission_is_saved_to_history(client, repository):
    submit(client)
    assert repository.count_valuations() == 1
    saved = repository.get_recent_valuations(1)[0]
    assert (saved["address"], saved["beds"], saved["sqft"], saved["recommended_rent"]) == ("123 Main St", 3, 1400, 2512)


def test_the_engine_is_called_once_per_submission(client):
    source = ListSource(matching(4))
    submit(client, source)
    assert source.calls == 1


def test_the_form_is_shown_again_below_the_result_with_its_values(client):
    html = submit(client).text
    assert "Run another valuation" in html
    assert html.index('id="result-card"') < html.index('id="valuation-form"')
    assert 'name="address"' in html and 'value="123 Main St"' in html and 'value="1400"' in html


def test_coordinates_are_passed_through_to_the_engine(client):
    source = ListSource(matching(3))
    # unknown address, but coordinates given, so no address lookup is needed
    response = submit(client, source, address="not a real address", latitude=str(LAT), longitude=str(LON))
    assert response.status_code == 200 and 'id="result-card"' in response.text


# --- confidence badge ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "n, level, css, label",
    [
        (3, "low", "text-bg-warning", "Low confidence"),
        (4, "low", "text-bg-warning", "Low confidence"),
        (5, "medium", "text-bg-info", "Medium confidence"),
        (9, "medium", "text-bg-info", "Medium confidence"),
        (10, "high", "text-bg-success", "High confidence"),
        (15, "high", "text-bg-success", "High confidence"),
    ],
)
def test_confidence_badge(client, n, level, css, label):
    html = submit(client, ListSource(matching(n))).text
    badge = re.search(r'<span class="badge ([^"]+)" id="confidence-badge" data-confidence="(\w+)"[^>]*>([^<]+)</span>', html)
    assert badge.groups() == (css, level, label)


def test_low_confidence_adds_a_rough_estimate_note(client):
    assert "rough estimate" in submit(client, ListSource(matching(3))).text
    other = TestClient(app)
    assert 'id="low-confidence-note"' not in submit(other, ListSource(matching(10))).text


def test_there_is_exactly_one_confidence_badge(client):
    assert submit(client).text.count('id="confidence-badge"') == 1


# --- funnel -------------------------------------------------------------------------------

def mixed():
    far = [comp(2600 + i, latitude=40.7, longitude=-74.0) for i in range(3)]
    wrong_beds = [comp(1500 + i, beds=6) for i in range(2)]
    too_big = [comp(3900, sqft=3000)]
    far_and_wrong = [comp(1000, latitude=40.7, longitude=-74.0, beds=9)]
    good = [comp(r) for r in (2000, 2100, 2200, 2300, 2400)]
    return far + wrong_beds + too_big + far_and_wrong + good + [comp(20000)]


def funnel_of(html):
    rows = re.findall(r'<li class="list-group-item px-0">\s*<div[^>]*>\s*<span>([^<]+)</span>\s*<span[^>]*>(\d+)', html)
    return [(label, int(count)) for label, count in rows]


def test_funnel_shows_every_stage_with_its_count(client):
    html = submit(client, ListSource(mixed())).text
    assert funnel_of(html) == [
        ("Fetched from the data source", 13),
        ("Within 1 mile of the property", 9),
        ("Similar bedrooms, bathrooms and size", 6),
        ("After removing unusual rents", 5),
        ("Used for pricing", 5),
    ]


def test_funnel_shows_what_was_lost_at_each_stage(client):
    html = submit(client, ListSource(mixed())).text
    assert html.count("(&minus;4)") == 1 and html.count("(&minus;3)") == 1 and html.count("(&minus;1)") == 1


def test_funnel_bars_are_percentages_of_what_was_fetched(client):
    html = submit(client, ListSource(mixed())).text
    assert re.findall(r'class="progress-bar" style="width: (\d+)%"', html) == ["100", "69", "46", "38", "38"]
    assert re.findall(r'aria-valuenow="(\d+)"', html) == ["100", "69", "46", "38", "38"]
    assert 'role="progressbar"' in html


# --- insufficient data --------------------------------------------------------------------

def test_insufficient_data_is_shown_instead_of_a_result(client):
    html_response = submit(client, ListSource(matching(2)))
    html = html_response.text
    assert html_response.status_code == 200
    assert 'id="insufficient-data"' in html and "Not enough comparable properties" in html
    assert 'id="result-card"' not in html and 'id="recommended-rent"' not in html


def test_insufficient_data_shows_the_counts(client):
    html = submit(client, ListSource(matching(2))).text
    counts = text_of(html, "insufficient-counts")
    assert counts == "Found 2 after filtering; at least 3 are needed for a reliable valuation."


def test_insufficient_data_has_no_confidence_badge(client):
    assert 'id="confidence-badge"' not in submit(client, ListSource(matching(2))).text


def test_insufficient_data_shows_the_funnel_and_an_explanation(client):
    source = ListSource([comp(2000), comp(2100)] + [comp(3000, latitude=40.7, longitude=-74.0)] * 4)
    html = submit(client, source).text
    assert funnel_of(html) == [
        ("Fetched from the data source", 6), ("Within 1 mile of the property", 2),
        ("Similar bedrooms, bathrooms and size", 2), ("After removing unusual rents", 2), ("Used for pricing", 2),
    ]
    assert "Only 2 of 6 listings are within 1 mile" in text_of(html, "insufficient-explanation")


@pytest.mark.parametrize("n, counts", [(0, "Found 0"), (1, "Found 1"), (2, "Found 2")])
def test_insufficient_for_zero_one_and_two(client, n, counts):
    html = submit(client, ListSource(matching(n))).text
    assert counts in text_of(html, "insufficient-counts")


def test_zero_comparables_explains_the_source_returned_nothing(client):
    assert "returned no listings" in text_of(submit(client, ListSource([])).text, "insufficient-explanation")


def test_insufficient_valuations_are_not_saved(client, repository):
    submit(client, ListSource(matching(2)))
    assert repository.count_valuations() == 0


def test_the_form_stays_available_after_insufficient_data(client):
    html = submit(client, ListSource(matching(2))).text
    assert 'value="123 Main St"' in html and 'id="submit-button"' in html


# --- invalid submissions ------------------------------------------------------------------

def test_invalid_submission_shows_errors_and_runs_nothing(client, repository):
    source = ListSource(matching(5))
    response = submit(client, source, beds="", sqft="0")
    assert response.status_code == 422
    assert text_of(response.text, "beds-error") == "Enter the number of bedrooms."
    assert text_of(response.text, "sqft-error") == "The square footage must be at least 1."
    assert source.calls == 0 and repository.count_valuations() == 0
    assert 'id="result-card"' not in response.text


def test_invalid_fields_are_marked_and_valid_ones_are_not(client):
    html = submit(client, beds="").text
    assert re.search(r'id="beds" name="beds"[^>]*>', html)
    assert 'form-control form-control-lg is-invalid" id="beds"' in html
    assert 'form-control form-control-lg" id="address"' in html


def test_entered_values_are_preserved_after_an_error(client):
    html = submit(client, address="9 Elm St", beds="", baths="1.5", sqft="900").text
    assert 'value="9 Elm St"' in html and 'value="1.5"' in html and 'value="900"' in html


def test_a_lone_coordinate_is_rejected_and_the_section_stays_open(client):
    html = submit(client, latitude="32.77").text
    assert "Enter both latitude and longitude" in text_of(html, "longitude-error")
    assert 'value="32.77"' in html
    assert re.search(r'<div class="collapse show mt-2" id="coordinates">', html)


def test_the_coordinates_section_is_closed_when_unused(client):
    html = client.get("/ui/").text
    assert '<div class="collapse mt-2" id="coordinates">' in html
    assert 'aria-expanded="false"' in html


def test_entered_text_is_escaped_in_the_form_and_the_result(client):
    nasty = '"><script>alert(1)</script>'
    html = submit(client, ListSource(matching(4)), address=nasty).text
    assert "<script>alert(1)</script>" not in html
    assert "&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_an_unknown_address_reports_a_field_error_and_opens_coordinates(client):
    response = submit(client, address="1 Nowhere Rd, Atlantis, CA")  # default mock geocoder, mock source
    assert response.status_code == 422
    assert "Or enter exact coordinates below." in text_of(response.text, "address-error")
    assert 'value="1 Nowhere Rd, Atlantis, CA"' in response.text
    assert 'class="collapse show mt-2" id="coordinates"' in response.text


# --- csrf -------------------------------------------------------------------------------------

def test_csrf_success(client):
    assert submit(client, ListSource(matching(4))).status_code == 200


def test_missing_token_is_rejected(client):
    client.get("/ui/")
    source = ListSource(matching(5))
    use_source(source)
    response = client.post("/ui/valuation", data=FORM)
    assert response.status_code == 403
    assert source.calls == 0


def test_wrong_token_is_rejected(client):
    source = ListSource(matching(5))
    response = submit(client, source, csrf_token="0" * 64)
    assert response.status_code == 403 and source.calls == 0


def test_a_post_without_the_cookie_is_rejected(client):
    token = token_of(client.get("/ui/"))
    fresh = TestClient(app)  # a different browser: no cookie
    use_source(ListSource(matching(5)))
    assert fresh.post("/ui/valuation", data={**FORM, "csrf_token": token}).status_code == 403


def test_a_token_taken_by_an_attacker_does_not_work_for_the_victim(client):
    """The attacker's own server fetches a token, then tricks the victim's browser into posting it."""
    attacker_token = token_of(TestClient(app).get("/ui/"))
    victim_cookie_page = client.get("/ui/")  # the victim has their own cookie
    source = ListSource(matching(5))
    use_source(source)
    response = client.post("/ui/valuation", data={**FORM, "csrf_token": attacker_token})
    assert response.status_code == 403 and source.calls == 0
    assert token_of(victim_cookie_page) != attacker_token


def test_rejection_keeps_the_entries_and_explains_how_to_continue(client):
    response = submit(client, address="9 Elm St", sqft="900", csrf_token="bad")
    assert response.status_code == 403
    assert 'value="9 Elm St"' in response.text and 'value="900"' in response.text
    assert "please submit again" in text_of(response.text, "page-alert")
    assert 'id="result-card"' not in response.text


def test_the_rejection_page_carries_a_working_new_token(client, monkeypatch):
    use_source(ListSource(matching(4)))
    page = client.get("/ui/")
    monkeypatch.setenv("UI_SECRET_KEY", "rotated")  # like an app restart: old tokens stop working
    rejected = client.post("/ui/valuation", data={**FORM, "csrf_token": token_of(page)})
    assert rejected.status_code == 403
    retry = client.post("/ui/valuation", data={**FORM, "csrf_token": token_of(rejected)})
    assert retry.status_code == 200 and 'id="result-card"' in retry.text


def test_csrf_is_checked_before_the_form_is_validated(client):
    response = submit(client, address="", csrf_token="bad")
    assert response.status_code == 403 and "address-error" not in response.text


def test_get_requests_need_no_token(client):
    assert client.get("/ui/").status_code == 200 and client.get("/ui/history").status_code == 200


# --- provider and unexpected errors -----------------------------------------------------------

@pytest.mark.parametrize(
    "error, status, message",
    [
        (RentCastUnavailableError("connection refused to 10.0.0.5"), 503, "unavailable; try again shortly"),
        (RentCastAuthError("key sk-secret-123 rejected"), 502, "rejected our credentials"),
    ],
)
def test_provider_failures_show_a_plain_message(client, error, status, message):
    response = submit(client, FailingSource(error))
    assert response.status_code == status
    assert message in text_of(response.text, "page-alert")
    assert "10.0.0.5" not in response.text and "sk-secret" not in response.text
    assert 'value="123 Main St"' in response.text  # entries kept so they can try again


def test_unexpected_errors_hide_technical_detail(client, repository):
    response = submit(client, FailingSource(RuntimeError("secret boom /etc/passwd")))
    assert response.status_code == 500
    assert text_of(response.text, "page-alert") == "Something went wrong. Please try again."
    assert "boom" not in response.text and "passwd" not in response.text and "Traceback" not in response.text
    assert repository.count_valuations() == 0


def test_a_misconfigured_provider_inside_the_engine_is_hidden(client):
    response = submit(client, FailingSource(ProviderConfigurationError("RENTCAST_API_KEY is required")))
    assert response.status_code == 503
    assert "RENTCAST_API_KEY" not in response.text and "not set up correctly" in response.text


def test_a_misconfigured_provider_at_startup_of_the_request_gets_an_html_error_page(client):
    def broken():
        raise ProviderConfigurationError("RENTCAST_API_KEY is required")

    app.dependency_overrides[get_comparable_source] = broken
    page = client.get("/ui/")
    response = client.post("/ui/valuation", data={**FORM, "csrf_token": token_of(page)})
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="error-message"' in response.text and "RENTCAST_API_KEY" not in response.text


def test_provider_errors_on_the_json_api_are_still_json(client):
    use_source(FailingSource(RentCastUnavailableError("down")))
    response = client.post("/valuation", json={"address": "1 A St", "beds": 3, "baths": 2, "sqft": 1400, "latitude": LAT, "longitude": LON})
    assert response.status_code == 503
    assert response.json() == {"detail": "The comparable data provider is unavailable; try again shortly"}


# --- error pages -----------------------------------------------------------------------------

def test_unknown_ui_page_gets_an_html_404(client):
    response = client.get("/ui/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert text_of(response.text, "error-message") == "That page doesn't exist."
    assert '<a class="btn btn-primary" href="/ui/">Back to the valuation form</a>' in response.text


def test_wrong_method_on_a_ui_page_gets_an_html_405(client):
    response = client.get("/ui/valuation")
    assert response.status_code == 405 and response.headers["content-type"].startswith("text/html")


def test_error_page_hides_technical_detail(client):
    html = client.get("/ui/nope").text
    assert "Not Found" not in html and "detail" not in html.lower() and "Traceback" not in html


def test_missing_static_files_and_unknown_api_paths_keep_their_json_404(client):
    for path in ("/ui/static/nope.css", "/nope"):
        response = client.get(path)
        assert response.status_code == 404 and response.json() == {"detail": "Not Found"}


def test_ui_urls_are_not_confused_with_lookalike_paths(client):
    assert client.get("/uix").json() == {"detail": "Not Found"}


# --- ingress ---------------------------------------------------------------------------------

def test_form_action_and_cookie_path_follow_the_ingress_prefix(client):
    response = client.get("/ui/", headers={"X-Ingress-Path": PREFIX})
    assert f'action="{PREFIX}/ui/valuation"' in response.text
    assert f"Path={PREFIX}/ui" in response.headers["set-cookie"]


def test_submitting_through_the_ingress_prefix_works(client):
    use_source(ListSource(matching(4)))
    headers = {"X-Ingress-Path": PREFIX}
    page = client.get(f"{PREFIX}/ui/", headers=headers)
    response = client.post(f"{PREFIX}/ui/valuation", data={**FORM, "csrf_token": token_of(page)}, headers=headers)
    assert response.status_code == 200 and 'id="result-card"' in response.text
    assert all(url.startswith(PREFIX) for url in re.findall(r'(?:href|src|action)="([^"]+)"', response.text))


def test_error_page_links_follow_the_ingress_prefix(client):
    response = client.get(f"{PREFIX}/ui/nope", headers={"X-Ingress-Path": PREFIX})
    assert response.status_code == 404
    assert f'href="{PREFIX}/ui/">Back to the valuation form' in response.text


# --- double submits and live-data notice ---------------------------------------------------------

def test_form_blocks_double_submits(client):
    html = client.get("/ui/").text
    assert 'id="valuation-form" method="post"' in html and "data-single-submit" in html
    assert "/ui/static/js/app.js" in html
    script = client.get("/ui/static/js/app.js")
    assert script.status_code == 200 and "data-single-submit" in script.text


def test_live_data_note_only_appears_for_rentcast(client, monkeypatch):
    assert 'id="live-data-note"' not in client.get("/ui/").text
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    html = client.get("/ui/").text
    assert 'id="live-data-note"' in html and "one request" in html


def test_viewing_the_rentcast_page_makes_no_provider_call(client, monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    assert client.get("/ui/").status_code == 200  # the no_network fixture would fail on any call


# --- mobile layout ---------------------------------------------------------------------------

def test_mobile_viewport_and_container(client):
    html = client.get("/ui/").text
    assert 'name="viewport" content="width=device-width, initial-scale=1' in html
    assert 'class="container-fluid app-container' in html


def test_fields_stack_on_phones_and_sit_side_by_side_from_sm_up(client):
    html = client.get("/ui/").text
    assert re.search(r'<div class="col-12">\s*<label for="address"', html)
    for name in ("beds", "baths", "sqft"):
        assert re.search(rf'<div class="col-12 col-sm-4">\s*<label for="{name}"', html)
    for name in ("latitude", "longitude"):
        assert re.search(rf'<div class="col-12 col-sm-6">\s*<label for="{name}"', html)


def test_inputs_are_large_and_use_the_right_mobile_keyboards(client):
    html = client.get("/ui/").text
    inputs = re.findall(r"<input [^>]*>", html)
    visible = [i for i in inputs if 'type="hidden"' not in i]
    assert len(visible) == 6 and all("form-control-lg" in i for i in visible)
    assert re.search(r'name="beds"[^>]*inputmode="numeric"|inputmode="numeric"[^>]*name="beds"', html)
    assert 'inputmode="decimal"' in html
    assert 'autocomplete="off"' in html


def test_submit_button_is_full_width_and_large(client):
    assert 'class="btn btn-primary btn-lg w-100" id="submit-button"' in client.get("/ui/").text


def test_result_figures_use_a_two_column_grid_on_phones(client):
    html = submit(client).text
    assert html.count('class="col-6 col-md-3"') == 4


def test_no_fixed_pixel_widths_that_could_force_sideways_scrolling(client):
    for html in (client.get("/ui/").text, submit(TestClient(app)).text, submit(TestClient(app), ListSource(matching(2))).text):
        for style in re.findall(r'style="([^"]*)"', html):
            assert "px" not in style.replace("height: .4rem", "")
        assert "<table" not in html  # nothing that needs horizontal scrolling


def test_funnel_and_result_are_single_column_blocks(client):
    html = submit(client).text
    assert 'class="list-group list-group-flush" id="funnel"' in html
    assert 'class="card shadow-sm mb-4" id="result-card"' in html


# --- the API is untouched ------------------------------------------------------------------------

def test_the_api_schema_still_lists_only_the_api(client):
    assert set(client.get("/openapi.json").json()["paths"]) == {"/", "/valuation", "/history", "/stats"}
