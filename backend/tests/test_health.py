from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(**overrides: object) -> TestClient:
    # _env_file=None isolates tests from any local .env file.
    settings = Settings(_env_file=None, **overrides)
    return TestClient(create_app(settings))


def test_health_ok_without_keys() -> None:
    response = make_client(GEMINI_API_KEY=None).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["config"]["gemini_api_key_set"] is False
    assert body["config"]["embedding_provider"] == "gemini"
    assert body["config"]["embedding_model"] == "gemini-embedding-001"
    assert body["config"]["embedding_dim"] == 768
    assert "openai_api_key_set" not in body["config"]


def test_health_never_exposes_keys() -> None:
    client = make_client(GEMINI_API_KEY="secret-gemini")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["config"]["gemini_api_key_set"] is True
    assert "secret-gemini" not in response.text


def test_cors_allows_frontend_origin() -> None:
    client = make_client(CORS_ORIGINS="http://localhost:3000,http://example.test")
    response = client.get("/health", headers={"Origin": "http://example.test"})
    assert response.headers["access-control-allow-origin"] == "http://example.test"


def test_embedding_settings_are_configurable() -> None:
    body = make_client(EMBEDDING_MODEL="gemini-embedding-2", EMBEDDING_DIM=1536).get("/health")
    assert body.json()["config"]["embedding_model"] == "gemini-embedding-2"
    assert body.json()["config"]["embedding_dim"] == 1536
