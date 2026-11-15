"""Text embedders: the ``Embedder`` protocol, the real ``GeminiEmbedder`` and a
deterministic ``FakeEmbedder`` for tests and CI.

``model`` and ``dim`` are stored on every chunk, so a change of either is detectable and
the affected chunks can be re-embedded (``app.evidence_store.reembed_stale_chunks``).
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.config import DEFAULT_EMBEDDING_DIM, Settings


class EmbeddingError(RuntimeError):
    """The embedding provider failed or returned something unusable."""


@runtime_checkable
class Embedder(Protocol):
    """Turns text into fixed-size vectors. Documents and queries may be embedded
    differently (asymmetric retrieval task types)."""

    model: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# Gemini's batch embed endpoint accepts up to 100 inputs per request.
GEMINI_MAX_BATCH = 100


class GeminiEmbedder:
    """Embeddings from the Gemini API via ``google-genai``.

    Uses task type ``RETRIEVAL_DOCUMENT`` for chunks and ``RETRIEVAL_QUERY`` for queries,
    and requests ``output_dimensionality=dim``. Inputs are sent in batches of
    ``batch_size``. Rate limits (429) and transient server errors (408/5xx) are retried by
    the SDK with exponential backoff (``max_attempts`` attempts per request). The client is
    created lazily, so constructing this without an API key is fine until it is used.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str,
        dim: int,
        *,
        batch_size: int = GEMINI_MAX_BATCH,
        max_attempts: int = 5,
        client: Any = None,
    ) -> None:
        if not 1 <= batch_size <= GEMINI_MAX_BATCH:
            raise ValueError(f"batch_size must be between 1 and {GEMINI_MAX_BATCH}")
        self.model = model
        self.dim = dim
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self._api_key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise EmbeddingError("GEMINI_API_KEY is not set; cannot call Gemini embeddings")
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(
                        attempts=self.max_attempts, initial_delay=1.0, max_delay=30.0
                    )
                ),
            )
        return self._client

    def _embed(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        client = self._get_client()
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = list(texts[i : i + self.batch_size])
            try:
                response = client.models.embed_content(
                    model=self.model, contents=batch, config=config
                )
            except Exception as exc:
                raise EmbeddingError(f"Gemini embedding request failed: {exc}") from exc
            embeddings = response.embeddings or []
            if len(embeddings) != len(batch):
                raise EmbeddingError(
                    f"Gemini returned {len(embeddings)} embeddings for {len(batch)} inputs"
                )
            for embedding in embeddings:
                values = embedding.values
                if values is None or len(values) != self.dim:
                    got = None if values is None else len(values)
                    raise EmbeddingError(f"expected a {self.dim}-dim embedding, got {got}")
                vectors.append([float(v) for v in values])
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT") if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


@dataclass
class FakeEmbedder:
    """Deterministic, offline embedder for tests: each text maps to a unit vector seeded
    by its SHA-256, so equal texts get equal vectors and different texts are near-orthogonal.
    Records every call so tests can assert that nothing was (re-)embedded."""

    dim: int = DEFAULT_EMBEDDING_DIM
    model: str = "fake-embedding"
    calls: list[list[str]] = field(default_factory=list)

    @property
    def embedded_texts(self) -> list[str]:
        return [t for call in self.calls for t in call]

    def vector(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        rng = random.Random(seed)
        raw = [rng.gauss(0.0, 1.0) for _ in range(self.dim)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self.calls.append(list(texts))
        return [self.vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.vector(text)


def make_embedder(settings: Settings) -> GeminiEmbedder:
    """The production embedder for these settings."""
    key = settings.GEMINI_API_KEY.get_secret_value() if settings.GEMINI_API_KEY else None
    return GeminiEmbedder(key, settings.EMBEDDING_MODEL, settings.EMBEDDING_DIM)
