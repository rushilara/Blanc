from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import DEFAULT_EMBEDDING_MODEL, Settings
from app.deps import EmbedderDep
from app.embeddings import (
    Embedder,
    EmbeddingError,
    FakeEmbedder,
    GeminiEmbedder,
    MediaInput,
    make_embedder,
)


class FakeModels:
    """Stands in for ``genai.Client().models``."""

    def __init__(
        self,
        dim: int,
        drop: int = 0,
        wrong_dim: bool = False,
        fail: bool = False,
        *,
        collapse: bool = False,
    ):
        self.dim, self.drop, self.wrong_dim, self.fail = dim, drop, wrong_dim, fail
        self.collapse = collapse
        self.requests: list[dict] = []

    def embed_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": contents, "config": config})
        if self.fail:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        size = self.dim + (1 if self.wrong_dim else 0)
        n = 1 if self.collapse else max(0, len(contents) - self.drop)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5] * size) for _ in range(n)])


def gemini(models: FakeModels, **kwargs) -> GeminiEmbedder:
    return GeminiEmbedder(
        None, "gemini-embedding-2", models.dim, client=SimpleNamespace(models=models), **kwargs
    )


def _content_texts(contents) -> list[str]:
    """Unwrap one-Content-per-text batches used for embedding-2."""
    texts: list[str] = []
    for item in contents:
        parts = getattr(item, "parts", None)
        if parts is not None:
            texts.append(parts[0].text)
        else:
            texts.append(item)
    return texts


def test_gemini_batches_and_sets_task_types() -> None:
    models = FakeModels(dim=8)
    embedder = gemini(models, batch_size=2)
    vectors = embedder.embed_documents(["a", "b", "c", "d", "e"])
    assert len(vectors) == 5 and all(len(v) == 8 for v in vectors)
    assert [_content_texts(r["contents"]) for r in models.requests] == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]
    config = models.requests[0]["config"]
    assert config.task_type == "RETRIEVAL_DOCUMENT"
    assert config.output_dimensionality == 8
    assert models.requests[0]["model"] == "gemini-embedding-2"

    assert len(embedder.embed_query("q")) == 8
    assert models.requests[-1]["config"].task_type == "RETRIEVAL_QUERY"
    assert embedder.embed_documents([]) == []


def test_gemini_media_is_one_call_per_item() -> None:
    models = FakeModels(dim=4)
    embedder = gemini(models)
    png = b"\x89PNG\r\n\x1a\n"
    pdf = b"%PDF-1.4\n"
    vectors = embedder.embed_media(
        [
            MediaInput(data=png, mime_type="image/png"),
            MediaInput(data=pdf, mime_type="application/pdf"),
        ]
    )
    assert len(vectors) == 2 and all(len(v) == 4 for v in vectors)
    assert len(models.requests) == 2
    first = models.requests[0]["contents"][0]
    assert first.inline_data.mime_type == "image/png"
    assert first.inline_data.data == png
    assert models.requests[0]["config"].output_dimensionality == 4
    assert models.requests[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert embedder.embed_media([]) == []


def test_gemini_collapsed_batch_raises() -> None:
    """embedding-2 may return one vector for a bare contents list; we must detect that."""
    with pytest.raises(EmbeddingError, match="aggregat"):
        gemini(FakeModels(4, collapse=True)).embed_documents(["a", "b"])


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
    media = MediaInput(data=b"abc", mime_type="image/png")
    assert fake.embed_media([media]) == FakeEmbedder(dim=64).embed_media([media])
    assert fake.embedded_media == [media]
    assert fake.embed_media([]) == [] and len(fake.media_calls) == 1


def test_embedder_dependency(app: FastAPI) -> None:
    @app.get("/_embedder")
    def which(embedder: EmbedderDep) -> dict[str, object]:
        return {"model": embedder.model, "dim": embedder.dim}

    with TestClient(app) as client:
        assert client.get("/_embedder").json() == {
            "model": DEFAULT_EMBEDDING_MODEL,
            "dim": 768,
        }
        app.state.embedder = FakeEmbedder(dim=768)
        assert client.get("/_embedder").json()["model"] == "fake-embedding"
