"""Home Assistant ingress support.

Ingress serves the app under a prefix such as /api/hassio_ingress/<token>/ and tells it the
prefix in the X-Ingress-Path header. The UI must put that prefix in front of every link, form
action and static URL it emits, so the middleware stores it as the ASGI `root_path`, and the
templates build URLs with the `u()` helper (see templating.py). Without the header (running
directly) the prefix is empty and nothing changes.
"""
import re

INGRESS_HEADER = b"x-ingress-path"

# A conservative path: starts with a single "/" (a leading "//" would make links point at
# another host), then only letters, digits and _ . - / (no scheme, no host, no quotes or
# spaces), no empty segments and no "." / ".." segments.
_SAFE_PREFIX = re.compile(r"^/(?!/)(?:[A-Za-z0-9_\-]|\.(?=[A-Za-z0-9_\-])|/(?=[A-Za-z0-9_\-]))*$")
_MAX_LENGTH = 200


def clean_ingress_path(raw: str | None) -> str:
    """Return a safe URL prefix without a trailing slash, or "" if the value is unusable."""
    if not raw:
        return ""
    value = raw.strip().rstrip("/")
    if not value or len(value) > _MAX_LENGTH or not _SAFE_PREFIX.match(value):
        return ""
    if any(part in (".", "..") for part in value.split("/")):
        return ""
    return value


class IngressMiddleware:
    """Pure ASGI middleware: copy a valid X-Ingress-Path header into scope["root_path"]."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            for name, value in scope.get("headers", []):
                if name == INGRESS_HEADER:
                    prefix = clean_ingress_path(value.decode("latin-1"))
                    if prefix:
                        scope = {**scope, "root_path": prefix}
                    break
        await self.app(scope, receive, send)
