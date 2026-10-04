"""Home Assistant ingress support.

Ingress serves the app under a prefix such as /api/hassio_ingress/<token>/ and tells it the
prefix in the X-Ingress-Path header. The UI must put that prefix in front of every link, form
action and static URL it emits, so the middleware stores it as scope["ingress_prefix"], and the
templates build URLs with the `ui_url()` helper (see templating.py). Without the header (running
directly) the prefix is empty and nothing changes.

The prefix is deliberately NOT stored in the ASGI `root_path`. Home Assistant removes the prefix
before forwarding, so the request path is just /ui/...; Starlette's mounts (the static files)
assume `root_path` is a prefix of the path and look for the wrong file when it is not, which
returned 404 for every stylesheet and script. Instead the middleware makes the path the same
whichever way a proxy forwards the request: if the prefix is still in the path it is removed.
Routing then never sees the prefix.
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


def _strip_prefix(path, prefix: str):
    """Remove `prefix` from the front of `path` (str or bytes) when it is there as whole path
    segments; otherwise return the path unchanged. The bare prefix becomes "/"."""
    is_bytes = isinstance(path, bytes)
    marker = prefix.encode() if is_bytes else prefix
    slash = b"/" if is_bytes else "/"
    if path == marker:
        return slash
    if path.startswith(marker + slash):
        return path[len(marker):]
    return path


class IngressMiddleware:
    """Pure ASGI middleware: record a valid X-Ingress-Path header as scope["ingress_prefix"].

    The request path is normalised to what the app serves (/ui/...): a prefix that is still
    there is removed, one that was already removed is left alone. `root_path` is not touched
    (see the module docstring for why).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            for name, value in scope.get("headers", []):
                if name == INGRESS_HEADER:
                    prefix = clean_ingress_path(value.decode("latin-1"))
                    if prefix:
                        scope = {**scope, "ingress_prefix": prefix, "path": _strip_prefix(scope["path"], prefix)}
                        if "raw_path" in scope and scope["raw_path"] is not None:
                            scope["raw_path"] = _strip_prefix(scope["raw_path"], prefix)
                    break
        await self.app(scope, receive, send)
