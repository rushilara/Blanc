"""Opt-in live test against the real Gemini embeddings API.

Marked ``live``: deselected by default (``pytest.ini``) and so never run in CI. Run it with
``make test-live`` from the repo root, which reads GEMINI_API_KEY from the root ``.env``.
Skips if no key is set.
"""

import math
from pathlib import Path

import pytest
from _paths import RAW

from app.config import Settings
from app.contracts import EvidenceRecord
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import make_embedder
from app.evidence_store import store_records
from app.models import Case
from app.search_index import vector_search

pytestmark = pytest.mark.live


def _cos(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v, strict=True))
    return dot / (math.sqrt(sum(a * a for a in u)) * math.sqrt(sum(b * b for b in v)))


def test_gemini_embeddings_live() -> None:
    settings = Settings()  # reads the repo-root .env and the environment
    if settings.GEMINI_API_KEY is None:
        pytest.skip("GEMINI_API_KEY is not set")
    embedder = make_embedder(settings)
    docs = embedder.embed_documents(
        ["Alex emails Jordan about delivering a document on Friday.", "The harbour is closed."]
    )
    query = embedder.embed_query("Who is delivering a document?")
    assert len(docs) == 2
    assert all(len(v) == settings.EMBEDDING_DIM for v in [*docs, query])
    assert _cos(query, docs[0]) > _cos(query, docs[1])


def test_store_and_search_with_gemini_live(tmp_path: Path) -> None:
    settings = Settings()
    if settings.GEMINI_API_KEY is None:
        pytest.skip("GEMINI_API_KEY is not set")
    embedder = make_embedder(settings)
    engine = make_engine(f"sqlite:///{tmp_path / 'live.db'}")
    init_db(engine, settings.EMBEDDING_DIM)
    try:
        with make_session_factory(engine)() as session:
            case = Case(title="Live")
            session.add(case)
            session.flush()
            records = [
                EvidenceRecord.model_validate(dict(r, case_id=case.case_id))
                for r in RAW["records"]
            ]
            result = store_records(session, case.case_id, records, embedder)
            session.commit()
            assert result.inserted == len(records)
            query = embedder.embed_query("Who might meet in the afternoon?")
            hits = vector_search(session, case.case_id, query, k=1)
            assert [h.record_id for h in hits] == ["rec-02"]
    finally:
        engine.dispose()
