"""BLA-60: the API needs the signed session cookie when BLANC_AUTH_SECRET is set."""

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import SESSION_COOKIE, sign_session, verify_session
from app.config import Settings
from app.main import create_app

SECRET = "s" * 40


def client(db_url: str, **overrides: object) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, DATABASE_URL=db_url, **overrides)))


def test_token_round_trip_and_rejections() -> None:
    later = int(time.time()) + 600
    token = sign_session("sarah", SECRET, later)
    assert verify_session(token, SECRET) == "sarah"
    assert verify_session(token, "t" * 40) is None  # other secret
    assert verify_session(sign_session("sarah", SECRET, int(time.time()) - 1), SECRET) is None
    body, sig = token.rsplit(".", 1)
    forged = body.replace(body.split(".")[1], "YWRtaW4") + "." + sig  # "admin"
    assert verify_session(forged, SECRET) is None
    for bad in (None, "", "v1.x.1", f"v2.{token[3:]}", token + "0", token.upper()):
        assert verify_session(bad, SECRET) is None


def test_frontend_token_format_matches() -> None:
    # The same literal is asserted in frontend/tests/auth.test.ts against signSession().
    token = "v1.c2FyYWg.2000000000.08edef82080061ad120b2c17ad05bf9a62c2e8feb1697eee8b8df7e693ea804d"
    assert token == sign_session("sarah", SECRET, 2000000000)
    assert verify_session(token, SECRET, now=1999999999) == "sarah"


def test_api_open_without_secret(db_url: str) -> None:
    with client(db_url) as c:
        assert c.get("/cases").status_code == 200
        assert c.get("/health").json()["config"]["auth_required"] is False


def test_api_requires_session_when_secret_set(db_url: str) -> None:
    with client(db_url, BLANC_AUTH_SECRET=SECRET) as c:
        health = c.get("/health")
        assert health.status_code == 200 and health.json()["config"]["auth_required"] is True
        assert SECRET not in health.text
        assert c.get("/cases").status_code == 401
        assert c.post("/cases", json={"title": "x"}).status_code == 401
        assert c.get("/docs").status_code == 401
        c.cookies.set(SESSION_COOKIE, sign_session("sarah", "t" * 40, int(time.time()) + 60))
        assert c.get("/cases").status_code == 401
        c.cookies.set(SESSION_COOKIE, sign_session("sarah", SECRET, int(time.time()) + 60))
        assert c.post("/cases", json={"title": "x"}).status_code in (200, 201)
        assert c.get("/cases").status_code == 200


def test_401_keeps_cors_headers_and_preflight_passes(db_url: str) -> None:
    origin = {"Origin": "http://localhost:3000"}
    with client(db_url, BLANC_AUTH_SECRET=SECRET) as c:
        denied = c.get("/cases", headers=origin)
        assert denied.status_code == 401
        assert denied.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert denied.headers["access-control-allow-credentials"] == "true"
        preflight = c.options("/cases", headers={**origin, "Access-Control-Request-Method": "POST"})
        assert preflight.status_code == 200


@pytest.mark.parametrize("value", ["short", "x" * 31])
def test_short_secret_refused_at_startup(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, BLANC_AUTH_SECRET=value)


def test_blank_secret_means_off() -> None:
    assert Settings(_env_file=None, BLANC_AUTH_SECRET="  ").BLANC_AUTH_SECRET is None
