import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ui.ingress import clean_ingress_path
from app.ui.templating import STATIC_DIR, TEMPLATES_DIR

client = TestClient(app)
PREFIX = "/api/hassio_ingress/AbC123xyz"
BOOTSTRAP = "/ui/static/vendor/bootstrap"


def links(html):
    """Every URL the page points the browser at (href, src, form action), minus bare anchors."""
    return re.findall(r'(?:href|src|action)="([^"]+)"', html)


@pytest.fixture(autouse=True)
def no_provider_env(monkeypatch):
    monkeypatch.delenv("DATA_PROVIDER", raising=False)


# --- the page renders ------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/ui", "/ui/"])
def test_ui_route_renders_html(path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.startswith("<!doctype html>")
    assert "<title>Valuation · Rent Pricing Tool</title>" in response.text


def test_ui_path_does_not_redirect():
    assert client.get("/ui", follow_redirects=False).status_code == 200


def test_page_is_mobile_ready():
    html = client.get("/ui/").text
    assert 'name="viewport" content="width=device-width, initial-scale=1' in html
    assert '<html lang="en"' in html


def test_navbar_has_brand_and_both_links():
    html = client.get("/ui/").text
    assert '<nav class="navbar' in html
    assert 'class="navbar-brand fw-semibold" href="/ui/">Rent Pricing Tool</a>' in html
    assert re.search(r'<a class="nav-link active"\s+href="/ui/" aria-current="page">Valuation</a>', html)
    assert re.search(r'<a class="nav-link"\s+href="/ui/history">History</a>', html)


def test_footer_shows_the_app_version():
    assert "Rent Pricing Tool v0.3.1" in client.get("/ui/").text


def test_history_link_target_exists_and_marks_itself_active():
    response = client.get("/ui/history")
    assert response.status_code == 200
    assert re.search(r'<a class="nav-link active"\s+href="/ui/history" aria-current="page">History</a>', response.text)
    assert "<title>History · Rent Pricing Tool</title>" in response.text


# --- the form --------------------------------------------------------------------------

def test_form_has_the_planned_fields():
    html = client.get("/ui/").text
    for name in ("address", "beds", "baths", "sqft", "latitude", "longitude"):
        assert f'name="{name}"' in html
        assert f'for="{name}"' in html  # every input has a label
    assert 'id="beds"' in html and 'inputmode="numeric"' in html
    assert 'inputmode="decimal"' in html and 'step="0.5"' in html
    assert 'id="coordinates"' in html and "collapse" in html


def test_form_submission_is_enabled():
    html = client.get("/ui/").text
    assert re.search(r'<button type="submit"[^>]*id="submit-button">', html)
    assert "disabled" not in re.search(r'<button type="submit".*?</button>', html, re.S).group(0)
    assert "not connected yet" not in html
    assert 'method="post" action="/ui/valuation"' in html


# --- data source badge -----------------------------------------------------------------

def badge(html):
    return re.search(r'<span id="data-source-badge" class="badge ([^"]+)"[^>]*>([^<]+)</span>', html).groups()


def test_badge_defaults_to_mock():
    css, label = badge(client.get("/ui/").text)
    assert label == "MOCK" and "text-bg-secondary" in css


@pytest.mark.parametrize("value, label", [("rentcast", "RENTCAST"), ("RentCast", "RENTCAST"), ("csv", "CSV")])
def test_badge_follows_data_provider(monkeypatch, value, label):
    monkeypatch.setenv("DATA_PROVIDER", value)
    css, shown = badge(client.get("/ui/").text)
    assert shown == label and "text-bg-success" in css


def test_badge_is_read_on_every_request(monkeypatch):
    assert badge(client.get("/ui/").text)[1] == "MOCK"
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    assert badge(client.get("/ui/").text)[1] == "RENTCAST"


def test_badge_shows_a_misconfigured_provider_instead_of_failing(monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "zillow")
    response = client.get("/ui/")
    assert response.status_code == 200
    css, label = badge(response.text)
    assert label == "MISCONFIGURED" and "text-bg-danger" in css


def test_rendering_the_page_never_builds_a_provider(monkeypatch):
    """The badge must not need an API key or touch RentCast."""
    monkeypatch.setenv("DATA_PROVIDER", "rentcast")
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    assert client.get("/ui/").status_code == 200


# --- Bootstrap is served locally, no CDN -----------------------------------------------

def test_page_loads_bootstrap_from_its_own_server():
    found = links(client.get("/ui/").text)
    assert f"{BOOTSTRAP}/bootstrap.min.css" in found
    assert f"{BOOTSTRAP}/bootstrap.bundle.min.js" in found
    assert "/ui/static/css/app.css" in found


@pytest.mark.parametrize("page", ["/ui/", "/ui/history"])
def test_no_external_urls_anywhere(page):
    html = client.get(page).text.lower()
    assert "http://" not in html and "https://" not in html and "//cdn" not in html
    for url in links(html):
        assert url.startswith("/") and not url.startswith("//")


def test_no_javascript_framework_or_tooling_references():
    html = client.get("/ui/").text.lower()
    for word in ("react", "vue", "angular", "jquery", "webpack", "node_modules"):
        assert word not in html


# --- static assets ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "path, content_type, banner",
    [
        (f"{BOOTSTRAP}/bootstrap.min.css", "text/css", "Bootstrap  v5.3."),
        (f"{BOOTSTRAP}/bootstrap.bundle.min.js", "javascript", "Bootstrap v5.3."),
        ("/ui/static/css/app.css", "text/css", "Small overrides"),
    ],
)
def test_static_assets_load(path, content_type, banner):
    response = client.get(path)
    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert banner in response.text[:400]


def test_bootstrap_is_a_real_full_build():
    assert len(client.get(f"{BOOTSTRAP}/bootstrap.min.css").content) > 150_000
    assert len(client.get(f"{BOOTSTRAP}/bootstrap.bundle.min.js").content) > 50_000


def test_bootstrap_files_match_the_recorded_checksums():
    import hashlib

    folder = STATIC_DIR / "vendor" / "bootstrap"
    recorded = dict(
        line.split()[::-1] for line in (folder / "VERSION").read_text().splitlines() if re.match(r"\s+[0-9a-f]{64}", line)
    )
    assert set(recorded) == {"bootstrap.min.css", "bootstrap.bundle.min.js"}
    for name, digest in recorded.items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
    assert (folder / "LICENSE").read_text().startswith("The MIT License")


def test_missing_static_file_is_404():
    assert client.get("/ui/static/nope.css").status_code == 404


@pytest.mark.parametrize("path", ["/ui/static/../config.py", "/ui/static/%2e%2e/config.py", "/ui/static/..%2f..%2fmain.py"])
def test_static_files_cannot_escape_the_static_folder(path):
    response = client.get(path)
    assert response.status_code in (400, 404)
    assert "APP_NAME" not in response.text


# --- ingress prefix handling -----------------------------------------------------------

def test_without_the_header_urls_have_no_prefix():
    assert all(url.startswith("/ui/") for url in links(client.get("/ui/").text))


def test_ingress_prefix_is_added_to_every_url():
    html = client.get("/ui/", headers={"X-Ingress-Path": PREFIX}).text
    urls = links(html)
    assert urls and all(url.startswith(f"{PREFIX}/ui/") for url in urls)
    assert f"{PREFIX}/ui/static/vendor/bootstrap/bootstrap.min.css" in urls
    assert f"{PREFIX}/ui/history" in urls
    assert f"{PREFIX}/ui/valuation" in urls  # the form action


def test_ingress_prefix_applies_on_the_history_page_too():
    urls = links(client.get("/ui/history", headers={"X-Ingress-Path": PREFIX}).text)
    assert all(url.startswith(f"{PREFIX}/ui/") for url in urls)


def test_trailing_slash_on_the_header_is_ignored():
    urls = links(client.get("/ui/", headers={"X-Ingress-Path": PREFIX + "/"}).text)
    assert f"{PREFIX}/ui/history" in urls and not any("//" in u for u in urls)


def test_prefix_does_not_leak_into_the_next_request():
    client.get("/ui/", headers={"X-Ingress-Path": PREFIX})
    assert all(url.startswith("/ui/") for url in links(client.get("/ui/").text))


def test_works_when_the_proxy_also_leaves_the_prefix_in_the_path():
    response = client.get(f"{PREFIX}/ui/", headers={"X-Ingress-Path": PREFIX})
    assert response.status_code == 200
    assert f"{PREFIX}/ui/history" in links(response.text)


@pytest.mark.parametrize(
    "raw",
    [
        "//evil.example", "http://evil.example", "javascript:alert(1)", "relative/path", "/a b",
        '/x"onmouseover="alert(1)', "/<script>alert(1)</script>", "/../..", "/a/../b", "/a//b",
        "/" + "a" * 300, "", "   ", "/",
    ],
)
def test_unsafe_ingress_headers_are_ignored(raw):
    html = client.get("/ui/", headers={"X-Ingress-Path": raw}).text
    assert all(url.startswith("/ui/") for url in links(html))
    assert "<script>alert" not in html and "evil" not in html and "onmouseover" not in html


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, ""), ("", ""), ("/", ""), (PREFIX, PREFIX), (PREFIX + "/", PREFIX),
        (" /api/hassio_ingress/x ", "/api/hassio_ingress/x"), ("/a.b-c_d/e", "/a.b-c_d/e"),
        ("//x", ""), ("/a/./b", ""), ("/..", ""), ("http://x", ""),
    ],
)
def test_clean_ingress_path(raw, expected):
    assert clean_ingress_path(raw) == expected


def test_ingress_header_does_not_change_the_json_api():
    plain = client.get("/").json()
    assert client.get("/", headers={"X-Ingress-Path": PREFIX}).json() == plain
    assert client.get("/stats", headers={"X-Ingress-Path": PREFIX}).status_code == client.get("/stats").status_code


# --- templates -------------------------------------------------------------------------

def test_expected_templates_exist():
    for name in ("base.html", "valuation_form.html", "history.html"):
        assert (TEMPLATES_DIR / name).is_file()


def test_templates_are_autoescaped():
    from app.ui.templating import templates

    rendered = templates.env.from_string("{{ x }}").render(x="<b>&\"'")
    assert "<b>" not in rendered and "&lt;b&gt;" in rendered


def test_pages_extend_the_base_layout():
    for name in ("valuation_form.html", "history.html"):
        assert (TEMPLATES_DIR / name).read_text().startswith('{% extends "base.html" %}')


# --- the API is untouched --------------------------------------------------------------

def test_ui_routes_are_hidden_from_the_api_schema():
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {"/", "/valuation", "/history", "/stats"}


def test_json_endpoints_are_unchanged():
    assert client.get("/").json() == {"app": "Rent Pricing Tool", "status": "online"}
    assert client.get("/history").headers["content-type"] == "application/json"


# --- dependencies ----------------------------------------------------------------------

def test_requirements_list_the_ui_dependencies():
    text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    assert re.search(r"^jinja2>=", text, re.M) and re.search(r"^python-multipart>=", text, re.M)
    for forbidden in ("react", "vue", "node", "webpack", "bootstrap"):
        assert not re.search(rf"^{forbidden}", text, re.M | re.I)


def test_template_and_form_libraries_import():
    import jinja2  # noqa: F401
    import python_multipart  # noqa: F401


# --- ingress: static files and pages when Home Assistant has stripped the prefix ------------------------
#
# Regression tests for the 404s seen in the deployed app. Supervisor removes the ingress prefix
# before forwarding, so the app sees /ui/static/... and the header X-Ingress-Path carries the prefix.

ASSETS = [
    ("/ui/static/css/app.css", "text/css"),
    ("/ui/static/js/app.js", "javascript"),
    ("/ui/static/vendor/bootstrap/bootstrap.min.css", "text/css"),
    ("/ui/static/vendor/bootstrap/bootstrap.bundle.min.js", "javascript"),
]


def via_ingress(path, method="get", client=client, **kwargs):
    """A request as the Supervisor forwards it: the prefix already removed, the header added."""
    headers = {**kwargs.pop("headers", {}), "X-Ingress-Path": PREFIX}
    return getattr(client, method)(path, headers=headers, **kwargs)


def without_prefix(url):
    assert url.startswith(PREFIX + "/"), url
    return url[len(PREFIX):]


@pytest.mark.parametrize("path, content_type", ASSETS)
def test_static_files_load_through_ingress_with_the_prefix_stripped(path, content_type):
    response = via_ingress(path)
    assert response.status_code == 200, "static file not found when the ingress prefix was already stripped"
    assert content_type in response.headers["content-type"]


@pytest.mark.parametrize("path, content_type", ASSETS)
def test_ingress_serves_the_same_bytes_as_direct_access(path, content_type):
    assert via_ingress(path).content == client.get(path).content


@pytest.mark.parametrize("path, content_type", ASSETS)
def test_static_files_still_load_directly_without_the_header(path, content_type):
    response = client.get(path)
    assert response.status_code == 200 and content_type in response.headers["content-type"]


@pytest.mark.parametrize("path, content_type", ASSETS)
def test_static_files_load_when_the_prefix_is_left_in_the_path(path, content_type):
    response = client.get(PREFIX + path, headers={"X-Ingress-Path": PREFIX})
    assert response.status_code == 200 and content_type in response.headers["content-type"]


def test_every_url_on_the_page_resolves_through_a_prefix_stripping_proxy():
    """Browser -> proxy -> app: the page links to the prefixed URLs, the proxy strips the prefix."""
    page = via_ingress("/ui/")
    assert page.status_code == 200
    urls = re.findall(r'(?:href|src)="([^"]+)"', page.text)
    assert len(urls) >= 6
    for url in urls:
        response = via_ingress(without_prefix(url))
        assert response.status_code == 200, f"{url} -> {response.status_code}"


def test_every_url_on_the_history_page_resolves_through_a_prefix_stripping_proxy():
    page = via_ingress("/ui/history")
    for url in re.findall(r'(?:href|src)="([^"]+)"', page.text):
        assert via_ingress(without_prefix(url)).status_code == 200, url


def test_the_page_is_styled_through_ingress_not_just_served():
    """The stylesheet the page points at is real Bootstrap, reachable at the prefixed URL."""
    page = via_ingress("/ui/").text
    css_url = re.search(r'href="([^"]*bootstrap\.min\.css)"', page).group(1)
    assert css_url.startswith(PREFIX)
    css = via_ingress(without_prefix(css_url))
    assert css.status_code == 200 and "Bootstrap" in css.text[:300] and len(css.content) > 150_000


def test_missing_static_files_are_still_404_through_ingress():
    response = via_ingress("/ui/static/nope.css")
    assert response.status_code == 404 and response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("path", ["/ui/static/../config.py", "/ui/static/%2e%2e/config.py", "/ui/static/..%2fmain.py"])
def test_static_path_traversal_is_still_blocked_through_ingress(path):
    response = via_ingress(path)
    assert response.status_code in (400, 404) and "APP_NAME" not in response.text


def test_html_error_pages_still_work_through_ingress():
    response = via_ingress("/ui/nope")
    assert response.status_code == 404 and response.headers["content-type"].startswith("text/html")
    assert f'href="{PREFIX}/ui/">Back to the valuation form' in response.text


def test_the_json_api_is_unchanged_through_ingress():
    assert via_ingress("/").json() == client.get("/").json()
    assert via_ingress("/nope").json() == {"detail": "Not Found"}


def test_a_form_submission_works_end_to_end_through_ingress():
    """GET the form and POST it with the prefix stripped, carrying the cookie the way a browser at
    the prefixed address would (the cookie's path is scoped to the prefix)."""
    fresh = TestClient(app)
    page = via_ingress("/ui/", client=fresh)
    cookie = page.headers["set-cookie"]
    assert f"Path={PREFIX}/ui" in cookie
    cookie_pair = cookie.split(";")[0]
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = via_ingress(
        "/ui/valuation", method="post", client=fresh, headers={"Cookie": cookie_pair},
        data={"address": "123 Main St", "beds": "3", "baths": "2", "sqft": "1400", "csrf_token": token},
    )
    assert response.status_code == 200 and 'id="result-card"' in response.text
    assert all(url.startswith(PREFIX) for url in re.findall(r'(?:href|src|action)="([^"]+)"', response.text))


# --- the middleware's contract ------------------------------------------------------------------------

def run_middleware(path, header=PREFIX, raw_path=None):
    """Run IngressMiddleware once and return the scope the application receives."""
    import asyncio

    from app.ui.ingress import IngressMiddleware

    seen = {}

    async def downstream(scope, receive, send):
        seen.update(scope)

    scope = {
        "type": "http", "path": path, "raw_path": (raw_path or path).encode(), "root_path": "",
        "headers": [(b"x-ingress-path", header.encode())] if header is not None else [],
    }
    asyncio.run(IngressMiddleware(downstream)(scope, None, None))
    return seen


def test_the_prefix_is_kept_out_of_root_path():
    """root_path is what Starlette mounts use to find their sub-path; a prefix the proxy already
    removed must not be put there, or StaticFiles looks for the wrong file."""
    scope = run_middleware("/ui/static/css/app.css")
    assert scope["root_path"] == ""
    assert scope["ingress_prefix"] == PREFIX


def test_a_stripped_path_is_left_alone():
    scope = run_middleware("/ui/static/css/app.css")
    assert scope["path"] == "/ui/static/css/app.css" and scope["raw_path"] == b"/ui/static/css/app.css"


def test_a_path_that_still_has_the_prefix_is_stripped_to_what_the_app_serves():
    scope = run_middleware(f"{PREFIX}/ui/static/css/app.css")
    assert scope["path"] == "/ui/static/css/app.css" and scope["raw_path"] == b"/ui/static/css/app.css"
    assert scope["root_path"] == "" and scope["ingress_prefix"] == PREFIX


def test_only_whole_path_segments_are_stripped():
    lookalike = f"{PREFIX}EXTRA/ui/"
    assert run_middleware(lookalike)["path"] == lookalike


def test_the_bare_prefix_becomes_the_root_path_of_the_app():
    assert run_middleware(PREFIX)["path"] == "/"


@pytest.mark.parametrize("header", [None, "", "//evil.example", "http://x", "/a b", "/.."])
def test_without_a_usable_header_nothing_changes(header):
    scope = run_middleware(f"{PREFIX}/ui/", header=header)
    assert scope["path"] == f"{PREFIX}/ui/" and scope["root_path"] == ""
    assert "ingress_prefix" not in scope


def test_query_strings_and_other_scope_values_are_untouched():
    import asyncio

    from app.ui.ingress import IngressMiddleware

    seen = {}

    async def downstream(scope, receive, send):
        seen.update(scope)

    scope = {"type": "http", "path": "/ui/", "query_string": b"a=1", "method": "GET", "client": ("1.2.3.4", 5),
             "headers": [(b"x-ingress-path", PREFIX.encode())]}
    asyncio.run(IngressMiddleware(downstream)(scope, None, None))
    assert seen["query_string"] == b"a=1" and seen["method"] == "GET" and seen["client"] == ("1.2.3.4", 5)
    assert "raw_path" not in seen  # not invented when the server did not provide one
