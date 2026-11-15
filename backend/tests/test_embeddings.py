from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import EmbedderDep
from app.embeddings import Embedder, EmbeddingError, FakeEmbedder, GeminiEmbedder, make_embedder


class FakeModels:
    """Stands in for ``genai.Client().models``."""

    def __init__(self, dim: int, drop: int = 0, wrong_dim: bool = False, fail: bool = False):
        self.dim, self.drop, self.wrong_dim, self.fail = dim, drop, wrong_dim, fail
        self.requests: list[dict] = []

    def embed_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": contents, "config": config})
        if self.fail:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        size = self.dim + (1 if self.wrong_dim else 0)
        items = contents[: len(contents) - self.drop]
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5] * size) for _ in items])


def gemini(models: FakeModels, **kwargs) -> GeminiEmbedder:
    return GeminiEmbedder(
        None, "gemini-embedding-001", models.dim, client=SimpleNamespace(models=models), **kwargs
    )


def test_gemini_batches_and_sets_task_types() -> None:
    models = FakeModels(dim=8)
    embedder = gemini(models, batch_size=2)
    vectors = embedder.embed_documents(["a", "b", "c", "d", "e"])
    assert len(vectors) == 5 and all(len(v) == 8 for v in vectors)
    assert [r["contents"] for r in models.requests] == [["a", "b"], ["c", "d"], ["e"]]
    config = models.requests[0]["config"]
    assert config.task_type == "RETRIEVAL_DOCUMENT"
    assert config.output_dimensionality == 8
    assert models.requests[0]["model"] == "gemini-embedding-001"

    assert len(embedder.embed_query("q")) == 8
    assert models.requests[-1]["config"].task_type == "RETRIEVAL_QUERY"
    assert embedder.embed_documents([]) == []


@pytest.mark.parametrize(
    "models", [FakeModels(4, drop=1), FakeModels(4, wrong_dim=True), FakeModels(4, fail=True)]
)
def test_gemini_bad_responses_raise(models: FakeModels) -> None:
    with pytest.raises(EmbeddingError):
        gemini(models).embed_documents(["a", "b"])


def test_gemini_without_key_fails_on_use_only() -> None:
    embedder = make_embedder(Settings(_env_file=None, GEMINI_API_KEY=None))
    assert isinstance(embedder, Embedder)
    with pytest.raises(EmbeddingError, match="GEMINI_API_KEY"):
        embedder.embed_query("x")


def test_make_embedder_uses_settings() -> None:
    settings = Settings(_env_file=None, GEMINI_API_KEY="k", EMBEDDING_MODEL="m", EMBEDDING_DIM=256)
    embedder = make_embedder(settings)
    assert (embedder.model, embedder.dim) == ("m", 256)
    client = embedder._get_client()  # constructing the client makes no request
    assert client is embedder._get_client()


def test_invalid_batch_size() -> None:
    with pytest.raises(ValueError):
        GeminiEmbedder(None, "m", 8, batch_size=101)


def test_fake_embedder_is_deterministic_unit_vectors() -> None:
    fake = FakeEmbedder(dim=64)
    assert isinstance(fake, Embedder)
    v1, v2 = fake.embed_documents(["same", "other"])
    assert v1 == FakeEmbedder(dim=64).embed_query("same")
    assert sum(x * x for x in v1) == pytest.approx(1.0)
    assert abs(sum(x * y for x, y in zip(v1, v2, strict=True))) < 0.5
    assert fake.embedded_texts == ["same", "other"]
    assert fake.embed_documents([]) == [] and len(fake.calls) == 1


def test_embedder_dependency(app: FastAPI) -> None:
    @app.get("/_embedder")
    def which(embedder: EmbedderDep) -> dict[str, object]:
        return {"model": embedder.model, "dim": embedder.dim}

    with TestClient(app) as client:
        assert client.get("/_embedder").json() == {"model": "gemini-embedding-001", "dim": 768}
        app.state.embedder = FakeEmbedder(dim=768)
        assert client.get("/_embedder").json()["model"] == "fake-embedding"
