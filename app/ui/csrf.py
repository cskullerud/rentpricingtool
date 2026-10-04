"""CSRF protection for the UI's POST form.

A valid-looking token alone is not enough, because an attacker's own server could fetch one
and put it in a forged form. So the token is bound to the visitor's browser: the server sets a
random value in an HttpOnly, SameSite=Lax cookie, and the form carries an HMAC of that value.
A cross-site page can neither read the cookie nor make the browser send it with a cross-site
POST, so it cannot produce a matching pair.

The HMAC key comes from UI_SECRET_KEY, or is random for each process. With a random key, forms
left open across an app restart are rejected (the user is asked to submit again, and nothing
they typed is lost).
"""
import hashlib
import hmac
import os
import re
import secrets

COOKIE_NAME = "rpt_csrf"
COOKIE_MAX_AGE = 8 * 60 * 60  # seconds
FORM_FIELD = "csrf_token"

_COOKIE_FORMAT = re.compile(r"^[A-Za-z0-9_\-]{20,100}$")
_process_secret = secrets.token_bytes(32)


def _secret() -> bytes:
    configured = os.getenv("UI_SECRET_KEY", "").strip()
    return configured.encode() if configured else _process_secret


def new_cookie_value() -> str:
    return secrets.token_urlsafe(32)


def is_well_formed(cookie_value: str | None) -> bool:
    return bool(cookie_value) and _COOKIE_FORMAT.match(cookie_value) is not None


def token_for(cookie_value: str) -> str:
    """The form token that goes with this cookie value."""
    return hmac.new(_secret(), cookie_value.encode(), hashlib.sha256).hexdigest()


def verify(cookie_value: str | None, token: str | None) -> bool:
    """True only if the cookie is well formed and the token is its HMAC. Constant-time compare."""
    if not is_well_formed(cookie_value) or not token:
        return False
    return hmac.compare_digest(token_for(cookie_value), token)
