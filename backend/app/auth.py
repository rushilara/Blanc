"""Sign-in check for the API (BLA-60).

The Next server signs people in (frontend/src/lib/auth.ts) and sets an HttpOnly
``blanc_session`` cookie. When ``BLANC_AUTH_SECRET`` is set, every API route except
``/health`` (and CORS preflights) needs that cookie with a valid signature, checked here
with the same secret. Token: ``v1.<base64url username>.<expiry unix seconds>.<hex
HMAC-SHA256 of everything before the last dot>``.

This gates the app and API on the investigator's machine. It does not encrypt the SQLite
file: someone with raw disk access needs OS accounts / disk encryption to stop them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

SESSION_COOKIE = "blanc_session"
MIN_SECRET_LENGTH = 32
OPEN_PATHS = frozenset({"/health"})

_TOKEN = re.compile(r"^v1\.([A-Za-z0-9_-]+)\.(\d{1,12})\.([0-9a-f]{64})$")


def sign_session(username: str, secret: str, expires_at: int) -> str:
    """Build a token (tests and tooling; the frontend issues the real ones)."""
    user = base64.urlsafe_b64encode(username.encode()).decode().rstrip("=")
    body = f"v1.{user}.{expires_at}"
    return f"{body}.{hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()}"


def verify_session(token: str | None, secret: str, now: float | None = None) -> str | None:
    """The signed-in username, or None for a missing, forged, malformed or expired token."""
    match = _TOKEN.match(token or "")
    if not match:
        return None
    user_b64, expiry, signature = match.groups()
    body = f"v1.{user_b64}.{expiry}"
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    if int(expiry) <= (time.time() if now is None else now):
        return None
    try:
        username = base64.urlsafe_b64decode(user_b64 + "=" * (-len(user_b64) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    return username or None


def require_session(
    secret: str,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """HTTP middleware: 401 for any non-open route without a valid session cookie."""

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
            return await call_next(request)
        user = verify_session(request.cookies.get(SESSION_COOKIE), secret)
        if user is None:
            return JSONResponse({"detail": "Sign in required."}, status_code=401)
        request.state.user = user
        return await call_next(request)

    return middleware
