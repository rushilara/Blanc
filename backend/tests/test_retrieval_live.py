"""Opt-in live test: hybrid retrieval with real Gemini embeddings (``make test-live``).

Marked ``live``: deselected by default and in CI. Skips if GEMINI_API_KEY is not set.
"""

from pathlib import Path

import pytest
from _paths import RAW

from app.config import Settings
from app.contracts import EvidenceRecord
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import make_embedder
from app.evidence_store import store_records
from app.models import Case
from app.retrieval import RetrievalConfig, retrieve

pytestmark = pytest.mark.live


def test_hybrid_retrieval_with_gemini_live(tmp_path: Path) -> None:
    settings = Settings()
    if settings.GEMINI_API_KEY is None:
        pytest.skip("GEMINI_API_KEY is not set")
    embedder = make_embedder(settings)
    engine = make_engine(f"sqlite:///{tmp_path / 'live.db'}")
    init_db(engine, settings.EMBEDDING_DIM)
    try:
        with make_session_factory(engine)() as session:
            case = Case(title="Live retrieval")
            session.add(case)
            session.flush()
            records = [
                EvidenceRecord.model_validate(dict(r, case_id=case.case_id))
                for r in RAW["records"]
            ]
            store_records(session, case.case_id, records, embedder)
            session.commit()
            config = RetrievalConfig.from_settings(settings)

            # Semantic only: no word of the question appears in rec-02.
            result = retrieve(
                session, case.case_id, "Who might gather later in the day?", embedder,
                config=config,
            )
            assert result.records[0].record_id == "rec-02"

            # Keyword + semantic agree.
            result = retrieve(
                session, case.case_id, "Who is delivering a document?", embedder, config=config
            )
            top = result.records[0]
            assert top.record_id == "rec-01"
            assert top.matched_by == "both"
            assert top.excerpts[0].text == RAW["records"][0]["content"]
            assert top.source_locator == "message 1, line 12"
    finally:
        engine.dispose()
