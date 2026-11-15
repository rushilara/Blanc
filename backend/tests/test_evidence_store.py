"""BLA-12: records and chunks persisted with embeddings, scoped to one case."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from _paths import RAW
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.config import DEFAULT_EMBEDDING_DIM
from app.contracts import EvidenceRecord
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import EmbeddingError, FakeEmbedder
from app.evidence_store import (
    CaseNotFoundError,
    EmbeddingIndexMismatchError,
    get_chunks,
    get_record,
    list_chunks,
    list_records,
    reembed_stale_chunks,
    stale_chunk_count,
    store_records,
)
from app.ids import id_pattern
from app.models import Case, EvidenceChunk, EvidenceRecordRow
from app.search_index import FTS_TABLE, VEC_TABLE, keyword_search, vector_search

LONG_WORDS = 1200  # ~6000+ chars, several 1600-char windows


def long_text(tag: str) -> str:
    return " ".join(f"{tag}{i:04d}" for i in range(LONG_WORDS))


def record(case_id: str, record_id: str, content: str, **extra: object) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "record_id": record_id,
            "case_id": case_id,
            "source_file": "notes.txt",
            "source_locator": f"line {record_id}",
            "content": content,
            **extra,
        }
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "store.db"


def open_engine(db_path: Path, dim: int = DEFAULT_EMBEDDING_DIM) -> Engine:
    engine = make_engine(f"sqlite:///{db_path}")
    init_db(engine, dim)
    return engine


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    engine = open_engine(db_path)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with make_session_factory(engine)() as session:
        yield session


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def cases(session: Session) -> tuple[str, str]:
    a, b = Case(title="Case A"), Case(title="Case B")
    session.add_all([a, b])
    session.commit()
    return a.case_id, b.case_id


def count(session: Session, table: str, case_id: str | None = None) -> int:
    where = " WHERE case_id = :case_id" if case_id else ""
    return session.execute(
        text(f"SELECT count(*) FROM {table}{where}"), {"case_id": case_id}
    ).scalar_one()


def seed_two_cases(session, cases, embedder):
    a, b = cases
    store_records(
        session,
        a,
        [record(a, "rec-1", "Alex emails Jordan about the warehouse key."),
         record(a, "rec-2", long_text("alpha"))],
        embedder,
    )
    # Case B reuses record IDs: record IDs are only unique within a case.
    store_records(
        session,
        b,
        [record(b, "rec-1", "Casey meets Morgan at the harbour office."),
         record(b, "rec-9", "Morgan mentions the warehouse key too.")],
        embedder,
    )
    session.commit()


# --- Storage and case isolation ---------------------------------------------------------


def test_store_fixture_records(session, cases, embedder) -> None:
    a, _ = cases
    raw = [dict(r, case_id=a) for r in RAW["records"]]
    records = [EvidenceRecord.model_validate(r) for r in raw]
    result = store_records(session, a, records, embedder)
    session.commit()
    assert (result.inserted, result.unchanged, result.rejected) == (len(raw), 0, [])
    assert result.chunks_created == len(raw)  # short records: one chunk each
    assert [r.to_contract_json() for r in list_records(session, a)] == [
        r.to_contract_json() for r in sorted(records, key=lambda r: r.record_id)
    ]
    chunk_re = id_pattern("chk")
    for chunk in list_chunks(session, a):
        assert re.match(chunk_re, chunk.chunk_id)
        assert chunk.embedding_model == embedder.model
        assert chunk.embedding_dim == embedder.dim
        parent = get_record(session, a, chunk.record_id)
        assert parent is not None
        assert parent.content[chunk.char_start : chunk.char_end] == chunk.text
    assert count(session, VEC_TABLE, a) == count(session, FTS_TABLE, a) == len(raw)


def test_records_and_chunks_are_scoped_to_their_case(session, cases, embedder) -> None:
    a, b = cases
    seed_two_cases(session, cases, embedder)

    assert {r.record_id for r in list_records(session, a)} == {"rec-1", "rec-2"}
    assert {r.record_id for r in list_records(session, b)} == {"rec-1", "rec-9"}
    assert get_record(session, a, "rec-1").content.startswith("Alex")
    assert get_record(session, b, "rec-1").content.startswith("Casey")
    assert get_record(session, b, "rec-2") is None  # exists only in A
    assert get_record(session, a, "rec-9") is None

    a_chunks = list_chunks(session, a)
    b_chunks = list_chunks(session, b)
    assert {c.case_id for c in a_chunks} == {a}
    assert {c.case_id for c in b_chunks} == {b}
    assert list_chunks(session, b, "rec-2") == []
    # Looking up A's chunk IDs through B finds nothing.
    assert get_chunks(session, b, [c.chunk_id for c in a_chunks]) == []
    assert len(get_chunks(session, a, [c.chunk_id for c in a_chunks])) == len(a_chunks)
    assert count(session, VEC_TABLE, a) == len(a_chunks)
    assert count(session, VEC_TABLE, b) == len(b_chunks)


def test_vector_search_never_crosses_cases(session, cases, embedder) -> None:
    a, b = cases
    seed_two_cases(session, cases, embedder)
    b_text = "Casey meets Morgan at the harbour office."
    query = embedder.embed_query(b_text)  # identical to B's rec-1 vector

    b_hits = vector_search(session, b, query, k=5)
    assert b_hits[0].record_id == "rec-1"
    assert b_hits[0].distance == pytest.approx(0.0, abs=1e-5)

    a_chunk_ids = {c.chunk_id for c in list_chunks(session, a)}
    a_hits = vector_search(session, a, query, k=50)
    assert a_hits, "case A has chunks, so the KNN must return some"
    assert {h.chunk_id for h in a_hits} <= a_chunk_ids
    assert all(h.distance > 0.5 for h in a_hits)  # B's perfect match is not visible


def test_keyword_search_never_crosses_cases(session, cases, embedder) -> None:
    a, b = cases
    seed_two_cases(session, cases, embedder)
    a_ids = {c.chunk_id for c in list_chunks(session, a)}
    b_ids = {c.chunk_id for c in list_chunks(session, b)}

    # "warehouse" appears in both cases.
    a_hits = keyword_search(session, a, '"warehouse"')
    b_hits = keyword_search(session, b, '"warehouse"')
    assert [h.record_id for h in a_hits] == ["rec-1"]
    assert [h.record_id for h in b_hits] == ["rec-9"]
    assert {h.chunk_id for h in a_hits} <= a_ids
    assert {h.chunk_id for h in b_hits} <= b_ids
    # A term only in B is never found through A.
    assert keyword_search(session, a, '"harbour"') == []
    assert keyword_search(session, a, '"alpha0007"')[0].record_id == "rec-2"


# --- Re-import, changed content, rejections ---------------------------------------------


def test_restoring_same_records_is_a_noop(session, cases, embedder) -> None:
    a, _ = cases
    records = [record(a, "rec-1", "short"), record(a, "rec-2", long_text("beta"))]
    first = store_records(session, a, records, embedder)
    session.commit()
    calls = len(embedder.calls)
    rows, chunks = count(session, "evidence_records"), count(session, "evidence_chunks")
    vectors, fts = count(session, VEC_TABLE), count(session, FTS_TABLE)

    again = store_records(session, a, [r.model_copy() for r in records], embedder)
    session.commit()
    assert (first.inserted, again.inserted, again.unchanged, again.chunks_created) == (2, 0, 2, 0)
    assert again.rejected == []
    assert len(embedder.calls) == calls  # nothing re-embedded
    assert count(session, "evidence_records") == rows
    assert count(session, "evidence_chunks") == chunks
    assert (count(session, VEC_TABLE), count(session, FTS_TABLE)) == (vectors, fts)


def test_null_timestamp_and_absent_timestamp_are_the_same_record(session, cases, embedder):
    a, _ = cases
    store_records(session, a, [record(a, "rec-1", "x", timestamp=None)], embedder)
    result = store_records(session, a, [record(a, "rec-1", "x")], embedder)
    assert (result.unchanged, result.rejected) == (1, [])


def test_changed_content_for_existing_record_is_rejected(session, cases, embedder) -> None:
    a, _ = cases
    store_records(session, a, [record(a, "rec-1", "original text")], embedder)
    session.commit()
    calls = len(embedder.calls)

    result = store_records(
        session,
        a,
        [record(a, "rec-1", "edited text"), record(a, "rec-2", "new record")],
        embedder,
    )
    session.commit()
    assert result.inserted == 1
    assert [r.record_id for r in result.rejected] == ["rec-1"]
    assert "immutable" in result.rejected[0].reason
    assert "(fields: content)" in result.rejected[0].reason
    assert result.source_changed == []
    assert get_record(session, a, "rec-1").content == "original text"
    assert [c.text for c in list_chunks(session, a, "rec-1")] == ["original text"]
    assert embedder.calls[calls:] == [["new record"]]

    # Metadata changes count as different content too.
    meta = store_records(session, a, [record(a, "rec-2", "new record", metadata={})], embedder)
    assert [r.record_id for r in meta.rejected] == ["rec-2"]
    assert "(fields: metadata)" in meta.rejected[0].reason


SHA_OLD, SHA_NEW = "a" * 64, "b" * 64


def test_only_source_file_hash_changed_is_unchanged(session, cases, embedder) -> None:
    """A source file was edited (e.g. messages appended): its existing records arrive with
    a new original_sha256 but are otherwise identical. Keep the stored version, report it."""
    a, _ = cases
    store_records(session, a, [record(a, "rec-1", "hello", original_sha256=SHA_OLD)], embedder)
    session.commit()
    calls = len(embedder.calls)
    rows, chunks = count(session, "evidence_records"), count(session, "evidence_chunks")

    result = store_records(
        session, a, [record(a, "rec-1", "hello", original_sha256=SHA_NEW)], embedder
    )
    session.commit()
    assert (result.inserted, result.unchanged, result.chunks_created) == (0, 1, 0)
    assert (result.rejected, result.source_changed) == ([], ["rec-1"])
    assert len(embedder.calls) == calls
    assert get_record(session, a, "rec-1").original_sha256 == SHA_OLD  # not overwritten
    assert count(session, "evidence_records") == rows
    assert count(session, "evidence_chunks") == chunks

    # Adding or dropping the file hash is the same kind of change.
    added = store_records(session, a, [record(a, "rec-2", "x")], embedder)
    assert added.inserted == 1
    again = store_records(session, a, [record(a, "rec-2", "x", original_sha256=SHA_NEW)], embedder)
    assert (again.unchanged, again.source_changed, again.rejected) == (1, ["rec-2"], [])


def test_content_and_file_hash_changed_is_rejected_naming_fields(session, cases, embedder):
    a, _ = cases
    stored = record(a, "rec-1", "hello", original_sha256=SHA_OLD, timestamp="2024-01-01")
    store_records(session, a, [stored], embedder)
    result = store_records(
        session,
        a,
        [record(a, "rec-1", "hi", original_sha256=SHA_NEW, timestamp="2024-01-02")],
        embedder,
    )
    assert result.source_changed == []
    assert [r.record_id for r in result.rejected] == ["rec-1"]
    assert "(fields: content, original_sha256, timestamp)" in result.rejected[0].reason
    assert get_record(session, a, "rec-1").content == "hello"


def test_mixed_batch_after_source_file_edit(session, cases, embedder) -> None:
    """Re-import of an edited chat export: old messages (new file hash), one edited message,
    one appended message, one exact duplicate."""
    a, _ = cases
    store_records(
        session,
        a,
        [
            record(a, "m1", "first", original_sha256=SHA_OLD),
            record(a, "m2", "second", original_sha256=SHA_OLD),
            record(a, "m3", "third", original_sha256=SHA_OLD),
        ],
        embedder,
    )
    session.commit()
    calls = len(embedder.calls)

    result = store_records(
        session,
        a,
        [
            record(a, "m1", "first", original_sha256=SHA_NEW),  # source changed only
            record(a, "m2", "second (edited)", original_sha256=SHA_NEW),  # rejected
            record(a, "m3", "third", original_sha256=SHA_OLD),  # exact duplicate
            record(a, "m4", "fourth", original_sha256=SHA_NEW),  # new
        ],
        embedder,
    )
    session.commit()
    assert (result.inserted, result.unchanged, result.chunks_created) == (1, 2, 1)
    assert result.source_changed == ["m1"]
    assert [r.record_id for r in result.rejected] == ["m2"]
    assert "(fields: content, original_sha256)" in result.rejected[0].reason
    assert embedder.calls[calls:] == [["fourth"]]
    assert get_record(session, a, "m1").original_sha256 == SHA_OLD
    assert get_record(session, a, "m2").content == "second"


def test_file_hash_only_duplicate_within_one_call(session, cases, embedder) -> None:
    a, _ = cases
    result = store_records(
        session,
        a,
        [
            record(a, "rec-1", "one", original_sha256=SHA_OLD),
            record(a, "rec-1", "one", original_sha256=SHA_NEW),
            record(a, "rec-1", "two", original_sha256=SHA_OLD),
        ],
        embedder,
    )
    assert (result.inserted, result.unchanged, result.source_changed) == (1, 1, ["rec-1"])
    assert [r.record_id for r in result.rejected] == ["rec-1"]
    assert "(fields: content)" in result.rejected[0].reason
    assert get_record(session, a, "rec-1").original_sha256 == SHA_OLD


def test_duplicate_record_ids_within_one_call(session, cases, embedder) -> None:
    a, _ = cases
    result = store_records(
        session,
        a,
        [record(a, "rec-1", "one"), record(a, "rec-1", "one"), record(a, "rec-1", "two")],
        embedder,
    )
    assert (result.inserted, result.unchanged, len(result.rejected)) == (1, 1, 1)
    assert count(session, "evidence_records") == 1


def test_record_with_other_case_id_is_rejected(session, cases, embedder) -> None:
    a, b = cases
    result = store_records(
        session, a, [record(b, "rec-1", "belongs to B"), record(a, "rec-2", "ok")], embedder
    )
    session.commit()
    assert result.inserted == 1
    assert [r.record_id for r in result.rejected] == ["rec-1"]
    assert "does not match target case" in result.rejected[0].reason
    assert get_record(session, a, "rec-1") is None
    assert get_record(session, b, "rec-1") is None
    assert embedder.embedded_texts == ["ok"]


def test_unknown_case_raises(session, embedder) -> None:
    with pytest.raises(CaseNotFoundError):
        store_records(session, "case_missing", [record("case_missing", "r", "x")], embedder)
    assert embedder.calls == []


def test_embedder_dimension_must_match_index(session, cases) -> None:
    a, _ = cases
    with pytest.raises(EmbeddingIndexMismatchError):
        store_records(session, a, [record(a, "r", "x")], FakeEmbedder(dim=16))


def test_embedding_failure_writes_nothing(session, cases) -> None:
    a, _ = cases

    class Broken(FakeEmbedder):
        def embed_documents(self, texts):
            return [[0.0] * 3 for _ in texts]  # wrong size

    with pytest.raises(EmbeddingError):
        store_records(session, a, [record(a, "r", "x")], Broken())
    session.rollback()
    assert count(session, "evidence_records") == 0
    assert count(session, VEC_TABLE) == 0


def test_empty_content_is_stored_without_chunks(session, cases, embedder) -> None:
    a, _ = cases
    result = store_records(session, a, [record(a, "r", "   ")], embedder)
    assert (result.inserted, result.chunks_created) == (1, 0)
    assert get_record(session, a, "r").content == "   "
    assert embedder.calls == []
    assert store_records(session, a, [], embedder).inserted == 0


# --- Chunking -----------------------------------------------------------------------------


def test_long_record_has_overlapping_chunks_that_slice_back(session, cases, embedder) -> None:
    a, _ = cases
    content = long_text("gamma")
    store_records(session, a, [record(a, "long", content), record(a, "short", "tiny")], embedder)
    session.commit()

    chunks = list_chunks(session, a, "long")
    assert len(chunks) >= 4
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(content)
    for chunk in chunks:
        assert content[chunk.char_start : chunk.char_end] == chunk.text
        assert len(chunk.text) <= 1600
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert prev.char_start < nxt.char_start < prev.char_end  # overlaps, moves forward
        assert 100 <= prev.char_end - nxt.char_start <= 200  # ~50 tokens of overlap
    short = list_chunks(session, a, "short")
    assert [(c.char_start, c.char_end, c.text) for c in short] == [(0, 4, "tiny")]


# --- Durability and consistency -----------------------------------------------------------


def test_data_survives_restart(db_path, engine, session, cases, embedder) -> None:
    a, b = cases
    seed_two_cases(session, cases, embedder)
    before = {c.chunk_id for c in list_chunks(session, a)}
    session.close()
    engine.dispose()

    engine2 = open_engine(db_path)
    try:
        with make_session_factory(engine2)() as s2:
            assert {r.record_id for r in list_records(s2, a)} == {"rec-1", "rec-2"}
            assert {c.chunk_id for c in list_chunks(s2, a)} == before
            hits = vector_search(s2, a, embedder.embed_query(long_text("alpha")[:1600]), k=3)
            assert hits and {h.chunk_id for h in hits} <= before
            assert keyword_search(s2, b, '"harbour"')[0].record_id == "rec-1"
            # Re-importing after restart is still a no-op.
            calls = len(embedder.calls)
            again = store_records(s2, b, [record(b, "rec-1", "Casey meets Morgan at the "
                                                 "harbour office.")], embedder)
            assert (again.inserted, again.unchanged) == (0, 1)
            assert len(embedder.calls) == calls
    finally:
        engine2.dispose()


def test_deleting_a_case_leaves_no_orphans(session, cases, embedder) -> None:
    a, b = cases
    seed_two_cases(session, cases, embedder)
    b_chunks = count(session, VEC_TABLE, b)
    session.execute(text("DELETE FROM cases WHERE case_id = :a"), {"a": a})
    session.commit()
    for table in ("evidence_records", "evidence_chunks", VEC_TABLE, FTS_TABLE):
        assert count(session, table, a) == 0, table
    assert count(session, VEC_TABLE, b) == b_chunks
    assert count(session, FTS_TABLE, b) == b_chunks


def test_deleting_a_record_removes_its_vectors(session, cases, embedder) -> None:
    a, _ = cases
    seed_two_cases(session, cases, embedder)
    session.execute(
        text("DELETE FROM evidence_records WHERE case_id = :a AND record_id = 'rec-2'"), {"a": a}
    )
    session.commit()
    assert count(session, VEC_TABLE, a) == count(session, FTS_TABLE, a) == 1
    assert session.scalar(select(func.count()).select_from(EvidenceChunk)) == 3


def test_case_id_is_required_on_rows(session, cases) -> None:
    for table in (EvidenceRecordRow.__table__, EvidenceChunk.__table__):
        col = table.c.case_id
        assert not col.nullable
        assert {fk.column.table.name for fk in col.foreign_keys} >= {"cases"}


# --- Model change and re-embedding --------------------------------------------------------


def test_model_change_is_detected_and_reembedded(session, cases, embedder) -> None:
    a, b = cases
    seed_two_cases(session, cases, embedder)
    new = FakeEmbedder(model="fake-embedding-v2")
    total = len(list_chunks(session, a))
    assert stale_chunk_count(session, a, embedder) == 0
    assert stale_chunk_count(session, a, new) == total

    assert reembed_stale_chunks(session, a, new) == total
    session.commit()
    assert {c.embedding_model for c in list_chunks(session, a)} == {"fake-embedding-v2"}
    assert {c.embedding_model for c in list_chunks(session, b)} == {"fake-embedding"}
    assert count(session, VEC_TABLE, a) == total  # replaced, not duplicated
    assert reembed_stale_chunks(session, a, new) == 0


def test_dimension_change_rebuilds_index(db_path, engine, session, cases, embedder) -> None:
    a, _ = cases
    seed_two_cases(session, cases, embedder)
    total = len(list_chunks(session, a))
    session.close()
    engine.dispose()

    engine2 = open_engine(db_path, dim=32)
    try:
        with make_session_factory(engine2)() as s2:
            small = FakeEmbedder(dim=32)
            assert count(s2, VEC_TABLE) == 0  # old 768-dim vectors dropped
            assert stale_chunk_count(s2, a, small) == total
            with pytest.raises(EmbeddingIndexMismatchError):
                reembed_stale_chunks(s2, a, embedder)
            assert reembed_stale_chunks(s2, a, small) == total
            s2.commit()
            assert count(s2, VEC_TABLE, a) == total
            assert {c.embedding_dim for c in list_chunks(s2, a)} == {32}
            assert vector_search(s2, a, small.embed_query("x"), k=2)
    finally:
        engine2.dispose()


def test_reembed_unknown_case_raises(session, embedder) -> None:
    with pytest.raises(CaseNotFoundError):
        reembed_stale_chunks(session, "case_missing", embedder)
