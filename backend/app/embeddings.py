"""Text and media embedders: the ``Embedder`` protocol, the real ``GeminiEmbedder`` and a
deterministic ``FakeEmbedder`` for tests and CI.

``model`` and ``dim`` are stored on every chunk, so a change of either is detectable and
the affected chunks can be re-embedded (``app.evidence_store.reembed_stale_chunks``).

Default model is ``gemini-embedding-2`` (multimodal text + image + PDF) at
``output_dimensionality=768``. Do not mix ``gemini-embedding-001`` vectors in the same
index.

Batching note for embedding-2: a bare ``contents=[...]`` list of strings or Parts may
collapse to **one** aggregated vector. Text batches are sent as one ``Content`` per item
so each input still gets its own vector. Media is embedded one item per request (PDF
limit is one file; multiple Parts also aggregate).

``task_type`` note: embedding-2 ignores ``RETRIEVAL_DOCUMENT`` / ``RETRIEVAL_QUERY``, so
query and document vectors are currently symmetric. Owl / keyword baseline / "want more
evidence?" numbers measured on ``gemini-embedding-001`` (or any asymmetric setup) must be
re-run after this switch before quoting them.
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


@dataclass(frozen=True)
class MediaInput:
    """One image or PDF to embed: raw bytes and a MIME type."""

    data: bytes
    mime_type: str


@runtime_checkable
class Embedder(Protocol):
    """Turns text and media into fixed-size vectors. Documents and queries may be
    embedded differently (asymmetric retrieval task types)."""

    model: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_media(self, items: Sequence[MediaInput]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# Gemini's batch embed endpoint accepts up to 100 inputs per request.
GEMINI_MAX_BATCH = 100


class GeminiEmbedder:
    """Embeddings from the Gemini API via ``google-genai``.

    Still passes ``RETRIEVAL_DOCUMENT`` / ``RETRIEVAL_QUERY`` in config, but
    ``gemini-embedding-2`` does **not** honour ``task_type`` (Google docs): queries and
    documents are embedded the same way today. A query-side task prefix for text is a
    later option; do not invent one for multimodal Parts. Always requests
    ``output_dimensionality=dim`` (embedding-2's native default is 3072). Text inputs are
    sent in batches of ``batch_size``, each wrapped as its own ``Content`` so embedding-2
    returns one vector per text. Media is embedded one call at a time (PDFs truncated to
    Gemini's page limit first). Rate limits (429) and transient server errors (408/5xx)
    are retried by the SDK with exponential backoff (``max_attempts`` attempts per
    request). The client is created lazily, so constructing this without an API key is
    fine until it is used.
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

    def _config(self, task_type: str) -> Any:
        from google.genai import types

        return types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)

    def _vectors_from_response(self, response: Any, expected: int) -> list[list[float]]:
        embeddings = response.embeddings or []
        if len(embeddings) != expected:
            raise EmbeddingError(
                f"Gemini returned {len(embeddings)} embeddings for {expected} inputs "
                "(embedding-2 may aggregate a bare contents list into one vector; "
                "text batches must use one Content per item)"
            )
        vectors: list[list[float]] = []
        for embedding in embeddings:
            values = embedding.values
            if values is None or len(values) != self.dim:
                got = None if values is None else len(values)
                raise EmbeddingError(f"expected a {self.dim}-dim embedding, got {got}")
            vectors.append([float(v) for v in values])
        return vectors

    def _embed_texts(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        client = self._get_client()
        config = self._config(task_type)
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = list(texts[i : i + self.batch_size])
            # One Content per text: a list of raw strings/Parts can collapse to one vector
            # under gemini-embedding-2.
            contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in batch]
            try:
                response = client.models.embed_content(
                    model=self.model, contents=contents, config=config
                )
            except Exception as exc:
                raise EmbeddingError(f"Gemini embedding request failed: {exc}") from exc
            vectors.extend(self._vectors_from_response(response, len(batch)))
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed_texts(texts, "RETRIEVAL_DOCUMENT") if texts else []

    def embed_media(self, items: Sequence[MediaInput]) -> list[list[float]]:
        """One vector per media item. Calls are not batched: PDFs are one-file-per-request,
        and multiple Parts in one contents list aggregate into a single vector."""
        if not items:
            return []
        from google.genai import types

        from app.case_media import prepare_media_for_embedding

        client = self._get_client()
        config = self._config("RETRIEVAL_DOCUMENT")
        vectors: list[list[float]] = []
        for item in items:
            data, mime_type, _coverage = prepare_media_for_embedding(item.data, item.mime_type)
            contents = [types.Part.from_bytes(data=data, mime_type=mime_type)]
            try:
                response = client.models.embed_content(
                    model=self.model, contents=contents, config=config
                )
            except Exception as exc:
                raise EmbeddingError(f"Gemini media embedding request failed: {exc}") from exc
            vectors.extend(self._vectors_from_response(response, 1))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_texts([text], "RETRIEVAL_QUERY")[0]


@dataclass
class FakeEmbedder:
    """Deterministic, offline embedder for tests: each text (or media bytes) maps to a
    unit vector seeded by its SHA-256, so equal inputs get equal vectors and different
    inputs are near-orthogonal. Records every call so tests can assert that nothing was
    (re-)embedded."""

    dim: int = DEFAULT_EMBEDDING_DIM
    model: str = "fake-embedding"
    calls: list[list[str]] = field(default_factory=list)
    media_calls: list[list[MediaInput]] = field(default_factory=list)

    @property
    def embedded_texts(self) -> list[str]:
        return [t for call in self.calls for t in call]

    @property
    def embedded_media(self) -> list[MediaInput]:
        return [item for call in self.media_calls for item in call]

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

    def embed_media(self, items: Sequence[MediaInput]) -> list[list[float]]:
        if not items:
            return []
        self.media_calls.append(list(items))
        return [
            self.vector(f"media:{item.mime_type}:{hashlib.sha256(item.data).hexdigest()}")
            for item in items
        ]

    def embed_query(self, text: str) -> list[float]:
        return self.vector(text)


def make_embedder(settings: Settings) -> GeminiEmbedder:
    """The production embedder for these settings."""
    key = settings.GEMINI_API_KEY.get_secret_value() if settings.GEMINI_API_KEY else None
    return GeminiEmbedder(key, settings.EMBEDDING_MODEL, settings.EMBEDDING_DIM)
