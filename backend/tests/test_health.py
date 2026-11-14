from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(**overrides: object) -> TestClient:
    # _env_file=None isolates tests from any local .env file.
    settings = Settings(_env_file=None, **overrides)
    return TestClient(create_app(settings))


def test_health_ok_without_keys() -> None:
    response = make_client(GEMINI_API_KEY=None, OPENAI_API_KEY=None).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["config"]["gemini_api_key_set"] is False
    assert body["config"]["openai_api_key_set"] is False
    assert body["config"]["embedding_model"] == "text-embedding-3-small"


def test_health_never_exposes_keys() -> None:
    client = make_client(GEMINI_API_KEY="secret-gemini", OPENAI_API_KEY="secret-openai")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["config"]["gemini_api_key_set"] is True
    assert "secret-gemini" not in response.text
    assert "secret-openai" not in response.text


def test_cors_allows_frontend_origin() -> None:
    client = make_client(CORS_ORIGINS="http://localhost:3000,http://example.test")
    response = client.get("/health", headers={"Origin": "http://example.test"})
    assert response.headers["access-control-allow-origin"] == "http://example.test"
