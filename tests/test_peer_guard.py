import asyncio
import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.main import app as real_app
from app.security import (
    DEFAULT_SUPERVISOR_PEER,
    PeerGuardMiddleware,
    load_allowed_peers_from_env,
    parse_allowed_peers,
    peer_allowed,
)

ROOT = Path(__file__).resolve().parent.parent
SUPERVISOR = "172.30.32.2"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ALLOWED_PEERS", raising=False)


def tiny_app():
    return Starlette(routes=[Route("/", lambda request: PlainTextResponse("ok"))])


def client_for(app, host):
    return TestClient(app, client=(host, 51000))


# --- parsing the allow-list -----------------------------------------------------------------

def test_the_default_peer_is_the_supervisor():
    assert DEFAULT_SUPERVISOR_PEER == SUPERVISOR


@pytest.mark.parametrize(
    "raw, count",
    [("172.30.32.2", 1), ("172.30.32.2, 10.0.0.5", 2), ("172.30.32.0/23", 1), ("172.30.32.2;10.0.0.5", 2),
     ("172.30.32.2 10.0.0.5", 2), ("::1", 1), ("fd00::/64", 1)],
)
def test_valid_lists(raw, count):
    assert len(parse_allowed_peers(raw)) == count


@pytest.mark.parametrize(
    "raw",
    ["", "   ", ",", "not-an-ip", "172.30.32.2/24",  # host bits set: would silently widen the list
     "0.0.0.0/0", "::/0", "10.0.0.0/8", "172.16.0.0/12", "300.1.1.1", "172.30.32.2, nope"],
)
def test_invalid_or_too_broad_lists_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_allowed_peers(raw)


def test_unset_or_blank_environment_means_no_restriction(monkeypatch):
    assert load_allowed_peers_from_env() is None
    monkeypatch.setenv("ALLOWED_PEERS", "   ")
    assert load_allowed_peers_from_env() is None


def test_environment_value_is_parsed(monkeypatch):
    monkeypatch.setenv("ALLOWED_PEERS", SUPERVISOR)
    assert [str(n) for n in load_allowed_peers_from_env()] == [f"{SUPERVISOR}/32"]


def test_an_invalid_environment_value_raises_instead_of_disabling_the_check(monkeypatch):
    monkeypatch.setenv("ALLOWED_PEERS", "0.0.0.0/0")
    with pytest.raises(ValueError):
        load_allowed_peers_from_env()


# --- deciding about one peer ----------------------------------------------------------------

NETS = parse_allowed_peers("172.30.32.2, 10.1.0.0/24")


@pytest.mark.parametrize(
    "host, expected",
    [(SUPERVISOR, True), ("10.1.0.77", True), ("172.30.32.3", False), ("172.30.33.0", False),
     ("127.0.0.1", False), ("192.168.251.65", False), ("::1", False),
     ("::ffff:172.30.32.2", True), ("::ffff:172.30.33.0", False),
     ("testclient", False), ("", False), (None, False), ("172.30.32.2.evil.example", False),
     (" 172.30.32.2", False), ("172.30.32.2\n", False)],
)
def test_peer_allowed(host, expected):
    assert peer_allowed(host, NETS) is expected


# --- the middleware ---------------------------------------------------------------------------

def test_allowed_peer_gets_through():
    response = client_for(PeerGuardMiddleware(tiny_app(), allowed=SUPERVISOR), SUPERVISOR).get("/")
    assert (response.status_code, response.text) == (200, "ok")


@pytest.mark.parametrize("host", ["172.30.33.0", "127.0.0.1", "192.168.251.65", "8.8.8.8", "testclient"])
def test_other_peers_are_refused(host):
    response = client_for(PeerGuardMiddleware(tiny_app(), allowed=SUPERVISOR), host).get("/")
    assert response.status_code == 403
    assert response.text == "Forbidden" and response.headers["cache-control"] == "no-store"


def test_the_refusal_reveals_nothing():
    response = client_for(PeerGuardMiddleware(tiny_app(), allowed=SUPERVISOR), "172.30.33.0").get("/")
    assert SUPERVISOR not in response.text and "peer" not in response.text.lower() and "ok" not in response.text


def test_forwarding_headers_cannot_bypass_the_check():
    guarded = PeerGuardMiddleware(tiny_app(), allowed=SUPERVISOR)
    headers = {
        "X-Forwarded-For": SUPERVISOR, "X-Real-IP": SUPERVISOR, "Forwarded": f"for={SUPERVISOR}",
        "X-Ingress-Path": "/api/hassio_ingress/abc", "X-Forwarded-Host": SUPERVISOR,
    }
    assert client_for(guarded, "172.30.33.0").get("/", headers=headers).status_code == 403


def test_a_missing_client_address_is_refused():
    guarded = PeerGuardMiddleware(tiny_app(), allowed=SUPERVISOR)
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "client": None}
    asyncio.run(guarded(scope, receive, send))
    assert sent[0]["status"] == 403


def test_websocket_connections_from_other_peers_are_closed():
    guarded = PeerGuardMiddleware(tiny_app(), allowed=SUPERVISOR)
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "websocket", "path": "/", "headers": [], "client": ("172.30.33.0", 4000)}
    asyncio.run(guarded(scope, receive, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_lifespan_events_are_not_blocked():
    """Startup and shutdown have no peer; refusing them would stop the app from starting."""
    with client_for(PeerGuardMiddleware(tiny_app(), allowed=SUPERVISOR), "172.30.33.0"):
        pass


def test_no_restriction_when_no_list_is_configured():
    guarded = PeerGuardMiddleware(tiny_app())
    for host in ("172.30.33.0", "8.8.8.8", "testclient"):
        assert client_for(guarded, host).get("/").status_code == 200


def test_the_middleware_reads_the_environment_when_given_nothing(monkeypatch):
    monkeypatch.setenv("ALLOWED_PEERS", SUPERVISOR)
    guarded = PeerGuardMiddleware(tiny_app(), allowed=load_allowed_peers_from_env())
    assert client_for(guarded, SUPERVISOR).get("/").status_code == 200
    assert client_for(guarded, "172.30.33.0").get("/").status_code == 403


def test_an_invalid_list_fails_at_construction():
    with pytest.raises(ValueError):
        PeerGuardMiddleware(tiny_app(), allowed="0.0.0.0/0")


def test_refusals_are_logged_with_the_peer_address(caplog):
    with caplog.at_level(logging.WARNING, logger="app.security"):
        client_for(PeerGuardMiddleware(tiny_app(), allowed=SUPERVISOR), "172.30.33.9").get("/")
    assert "172.30.33.9" in caplog.text and "ALLOWED_PEERS" in caplog.text


# --- the real application behind the guard ---------------------------------------------------------

@pytest.fixture
def guarded_app():
    return PeerGuardMiddleware(real_app, allowed=SUPERVISOR)


@pytest.mark.parametrize("path", ["/", "/ui/", "/ui/history", "/ui/static/css/app.css", "/stats", "/history"])
def test_the_supervisor_can_reach_everything(guarded_app, path):
    assert client_for(guarded_app, SUPERVISOR).get(path).status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/", "/ui/", "/ui/history", "/ui/static/css/app.css", "/ui/static/vendor/bootstrap/bootstrap.min.css",
     "/stats", "/history", "/openapi.json", "/docs", "/nope"],
)
def test_other_add_ons_cannot_reach_anything(guarded_app, path):
    response = client_for(guarded_app, "172.30.33.5").get(path)
    assert response.status_code == 403 and response.text == "Forbidden"


def test_other_add_ons_cannot_post_valuations_or_forms(guarded_app):
    other = client_for(guarded_app, "172.30.33.5")
    body = {"address": "123 Main St", "beds": 3, "baths": 2, "sqft": 1400}
    assert other.post("/valuation", json=body).status_code == 403
    assert other.post("/ui/valuation", data={"address": "x"}).status_code == 403


def test_the_supervisor_can_still_submit_a_form(guarded_app):
    import re

    ingress = client_for(guarded_app, SUPERVISOR)
    page = ingress.get("/ui/")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = ingress.post(
        "/ui/valuation",
        data={"address": "123 Main St", "beds": "3", "baths": "2", "sqft": "1400", "csrf_token": token},
    )
    assert response.status_code == 200 and 'id="result-card"' in response.text


def test_ingress_prefix_handling_still_works_for_the_supervisor(guarded_app):
    prefix = "/api/hassio_ingress/AbC123xyz"
    response = client_for(guarded_app, SUPERVISOR).get("/ui/", headers={"X-Ingress-Path": prefix})
    assert f'href="{prefix}/ui/history"' in response.text


def test_the_rest_of_the_suite_runs_unrestricted_by_default():
    assert TestClient(real_app).get("/").status_code == 200  # the placeholder client "testclient"


# --- main.py wiring, in a fresh process --------------------------------------------------------------

def run_python(code, **env):
    import os

    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, capture_output=True, text=True, timeout=60,
        env={**{k: v for k, v in os.environ.items() if k != "ALLOWED_PEERS"}, **env},
    )


def test_main_enables_the_guard_from_the_environment():
    result = run_python(
        """
        from fastapi.testclient import TestClient
        from app.main import app
        ok = TestClient(app, client=("172.30.32.2", 1)).get("/").status_code
        other = TestClient(app, client=("172.30.33.0", 1)).get("/ui/").status_code
        print(ok, other)
        """,
        ALLOWED_PEERS=SUPERVISOR,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-2:] == ["200", "403"]


def test_main_refuses_to_start_with_an_invalid_list():
    result = run_python("import app.main", ALLOWED_PEERS="0.0.0.0/0")
    assert result.returncode != 0 and "too broad" in result.stderr


def test_main_is_unrestricted_when_the_variable_is_unset():
    result = run_python(
        """
        from fastapi.testclient import TestClient
        from app.main import app
        print(TestClient(app, client=("8.8.8.8", 1)).get("/").status_code)
        """
    )
    assert result.returncode == 0 and result.stdout.split()[-1] == "200"
