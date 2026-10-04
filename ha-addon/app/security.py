"""Network-peer allow-list, used when the app runs as a Home Assistant add-on.

Add-ons share one internal Docker network, so another add-on could call this app directly and
skip Home Assistant's ingress (and its login). When ALLOWED_PEERS is set, the app answers only
connections whose real source address is in the list; the add-on sets it to the Supervisor
(172.30.32.2), the address ingress requests come from. When it is unset (development) nothing
is checked.

The check uses the TCP peer address, never headers such as X-Forwarded-For, so a caller cannot
talk its way past it. (The server must not rewrite the peer address from proxy headers; the
add-on's entrypoint starts uvicorn with proxy_headers=False.) Unparseable or missing peer
addresses are refused.
"""
import ipaddress
import logging
import os
from collections.abc import Sequence

from starlette.responses import PlainTextResponse

logger = logging.getLogger(__name__)

ENV_VAR = "ALLOWED_PEERS"
DEFAULT_SUPERVISOR_PEER = "172.30.32.2"

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# Anything broader than this is almost certainly a mistake, and "allow everyone" must not be
# something a typo can configure.
_MIN_PREFIX_V4 = 16
_MIN_PREFIX_V6 = 64


def parse_allowed_peers(raw: str) -> tuple[Network, ...]:
    """Parse "172.30.32.2" or "172.30.32.0/23, 10.0.0.5" into networks.

    Raises ValueError for an empty list, a bad address, a network written with host bits set
    (such as 172.30.32.2/24, which would silently widen the list), or one that is too broad.
    """
    items = [part for part in raw.replace(";", ",").replace(" ", ",").split(",") if part]
    if not items:
        raise ValueError(f"{ENV_VAR} must list at least one address")
    networks = []
    for item in items:
        try:
            network = ipaddress.ip_network(item, strict=True)
        except ValueError:
            raise ValueError(f"{ENV_VAR} contains an invalid address or network: {item!r}") from None
        minimum = _MIN_PREFIX_V4 if network.version == 4 else _MIN_PREFIX_V6
        if network.prefixlen < minimum:
            raise ValueError(f"{ENV_VAR} entry {item!r} is too broad (the prefix must be /{minimum} or longer)")
        networks.append(network)
    return tuple(networks)


def load_allowed_peers_from_env() -> tuple[Network, ...] | None:
    """The configured allow-list, or None (no restriction) when ALLOWED_PEERS is unset or blank."""
    raw = os.getenv(ENV_VAR, "").strip()
    return parse_allowed_peers(raw) if raw else None


def peer_allowed(host: str | None, networks: Sequence[Network]) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False  # for example a hostname or a test client's placeholder
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in networks)


class PeerGuardMiddleware:
    """Pure ASGI middleware: refuse HTTP and WebSocket connections from peers not in the list."""

    def __init__(self, app, allowed: Sequence[Network] | str | None = None):
        self.app = app
        if isinstance(allowed, str):
            allowed = parse_allowed_peers(allowed)
        self.allowed = tuple(allowed) if allowed is not None else None

    async def __call__(self, scope, receive, send):
        if self.allowed is None or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)  # unrestricted, or lifespan events
            return
        client = scope.get("client")
        host = client[0] if client else None
        if peer_allowed(host, self.allowed):
            await self.app(scope, receive, send)
            return
        logger.warning("Refused a connection from %s: not in %s", host, ENV_VAR)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = PlainTextResponse("Forbidden", status_code=403, headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)
