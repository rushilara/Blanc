"""BLA-13: hybrid semantic + keyword retrieval, scoped to one case."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import Settings
from app.contracts import EvidenceRecord
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import FakeEmbedder
from app.evidence_store import (
    CaseNotFoundError,
    EmbeddingIndexMismatchError,
    list_chunks,
    list_records,
    store_records,
)
from app.models import Case
from app.retrieval import RetrievalConfig, fuse, retrieve, rrf_term
from app.search_index import (
    FTS_TABLE,
    FTS_TOKENIZE,
    MAX_FTS_TERMS,
    VEC_TABLE,
    KeywordHit,
    VectorHit,
    keyword_index_tokenizer,
    keyword_search,
    to_fts_query,
)

K = 60


@dataclass
class MappedEmbedder(FakeEmbedder):
    """FakeEmbedder whose queries can be pointed at a document: ``embed_query(q)`` returns
    the vector of ``queries[q]`` if mapped, so that document is the nearest neighbour."""

    queries: dict[str, str] = field(default_factory=dict)
    query_calls: list[str] = field(default_factory=list)

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self.vector(self.queries.get(text, text))


def record(case_id: str, record_id: str, content: str, **extra: object) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "record_id": record_id,
            "case_id": case_id,
            "source_file": f"{record_id}.txt",
            "source_locator": f"line {record_id}",
            "content": content,
            **extra,
        }
    )


def long_text(tag: str, words: int = 1200, inserts: dict[int, str] | None = None) -> str:
    inserts = inserts or {}
    return " ".join(inserts.get(i, f"{tag}{i:04d}") for i in range(words))


EMAIL_A = "Alex emails Jordan about delivering a document on Friday."
CHAT_A = "Jordan tells Casey there may be a meeting that afternoon."
NOTE_A = "John said it's the a-b route, near the café. (Zoë agreed: 10:30.)"
UNRELATED_A = "The cafeteria menu lists tomato soup on Tuesdays."
LONG_A = long_text("alpha", inserts={100: "harbour", 1000: "harbour"})
EMAIL_B = "Casey meets Morgan at the warehouse; the zeppelin docks at noon."


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = make_engine(f"sqlite:///{tmp_path / 'retrieval.db'}")
    init_db(engine, FakeEmbedder().dim)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with make_session_factory(engine)() as session:
        yield session


@pytest.fixture
def embedder() -> MappedEmbedder:
    return MappedEmbedder()


@pytest.fixture
def cases(session: Session, embedder: MappedEmbedder) -> tuple[str, str]:
    a, b = Case(title="Case A"), Case(title="Case B")
    session.add_all([a, b])
    session.flush()
    store_records(
        session,
        a.case_id,
        [
            record(a.case_id, "rec-email", EMAIL_A, timestamp="2026-04-10T09:15:00Z",
                   timestamp_kind="sent"),
            record(a.case_id, "rec-chat", CHAT_A),
            record(a.case_id, "rec-note", NOTE_A),
            record(a.case_id, "rec-unrelated", UNRELATED_A),
            record(a.case_id, "rec-long", LONG_A),
        ],
        embedder,
    )
    # Case B reuses a record ID with different content.
    store_records(session, b.case_id, [record(b.case_id, "rec-email", EMAIL_B)], embedder)
    session.commit()
    return a.case_id, b.case_id


def ids(result) -> list[str]:
    return [r.record_id for r in result.records]


def by_id(result, record_id):
    return next(r for r in result.records if r.record_id == record_id)


# --- Fusion math ------------------------------------------------------------------------


def vhits(*pairs: tuple[str, str]) -> list[VectorHit]:
    return [VectorHit(chunk_id=c, record_id=r, distance=0.1 * i) for i, (r, c) in enumerate(pairs)]


def khits(*pairs: tuple[str, str]) -> list[KeywordHit]:
    return [KeywordHit(chunk_id=c, record_id=r, score=-10.0 + i) for i, (r, c) in enumerate(pairs)]


def test_rrf_scores_match_the_formula() -> None:
    fused = fuse(
        vhits(("A", "a1"), ("B", "b1"), ("C", "c1")),
        khits(("C", "c1"), ("D", "d1")),
        rrf_k=K,
    )
    scores = {f.record_id: f.score for f in fused}
    assert scores["A"] == pytest.approx(1 / 61)
    assert scores["B"] == pytest.approx(1 / 62)
    assert scores["C"] == pytest.approx(1 / 63 + 1 / 61)
    assert scores["D"] == pytest.approx(1 / 62)
    assert [f.record_id for f in fused] == ["C", "A", "B", "D"]  # ties broken by record_id
    c = fused[0]
    assert c.matched_by == "both"
    assert (c.vector.rank, c.keyword.rank) == (3, 1)
    assert c.chunk_scores == {"c1": pytest.approx(1 / 63 + 1 / 61)}
    assert {f.record_id: f.matched_by for f in fused[1:]} == {
        "A": "vector", "B": "vector", "D": "keyword"
    }
    assert rrf_term(1, K) == pytest.approx(1 / 61)


def test_record_in_both_lists_outranks_single_list_records_at_similar_ranks() -> None:
    fused = fuse(vhits(("A", "a1"), ("B", "b1")), khits(("C", "c1"), ("B", "b2")))
    assert fused[0].record_id == "B"
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 62)
    assert fused[0].score > fused[1].score  # A and C: 1/61 each
    # Different chunks of B matched in each list; chunk scores keep them apart.
    assert fused[0].chunk_scores == {"b1": pytest.approx(1 / 62), "b2": pytest.approx(1 / 62)}


def test_record_rank_is_its_best_chunk_and_lists_are_deduped_to_records() -> None:
    fused = fuse(vhits(("A", "a1"), ("A", "a2"), ("A", "a3"), ("B", "b1")), [])
    scores = {f.record_id: f.score for f in fused}
    assert scores["A"] == pytest.approx(1 / 61)  # best chunk only, not a sum over chunks
    assert scores["B"] == pytest.approx(1 / 62)  # record rank 2, not chunk rank 4
    assert fused[0].vector.chunk_id == "a1"
    assert fused[0].chunk_scores == {
        "a1": pytest.approx(1 / 61), "a2": pytest.approx(1 / 62), "a3": pytest.approx(1 / 63)
    }


def test_weights_are_applied() -> None:
    fused = fuse(
        vhits(("A", "a1")), khits(("B", "b1")), rrf_k=10, vector_weight=1.0, keyword_weight=2.0
    )
    assert [(f.record_id, f.score) for f in fused] == [
        ("B", pytest.approx(2 / 11)), ("A", pytest.approx(1 / 11))
    ]


def test_config_from_settings() -> None:
    settings = Settings(_env_file=None, RETRIEVAL_MAX_RECORDS=12, RETRIEVAL_RRF_K=10)
    config = RetrievalConfig.from_settings(settings)
    assert (config.max_records, config.rrf_k) == (12, 10)
    assert RetrievalConfig.from_settings(Settings(_env_file=None)) == RetrievalConfig()


# --- End to end over the indexes --------------------------------------------------------


def test_hybrid_hit_ranks_first_with_exact_excerpt_and_locator(session, cases, embedder):
    a, _ = cases
    question = "Who mentioned a meeting?"
    embedder.queries[question] = CHAT_A  # vector rank 1 for rec-chat
    result = retrieve(session, a, question, embedder)

    top = result.records[0]
    assert top.record_id == "rec-chat"
    assert top.matched_by == "both"
    assert (result.skipped, result.budget_substituted) == ([], False)  # nothing left out
    assert (top.vector.rank, top.keyword.rank) == (1, 1)
    assert top.vector.score == pytest.approx(0.0, abs=1e-5)
    assert top.score == pytest.approx(2 / 61)
    assert (top.source_file, top.source_locator) == ("rec-chat.txt", "line rec-chat")
    assert [e.text for e in top.excerpts] == [CHAT_A]
    assert (top.excerpts[0].char_start, top.excerpts[0].char_end) == (0, len(CHAT_A))
    assert result.fts_query == '"mentioned" OR "meeting"'
    # Every stored record of case A has chunks, so the vector KNN returns all of them.
    assert sorted(ids(result)) == ["rec-chat", "rec-email", "rec-long", "rec-note", "rec-unrelated"]
    assert result.keyword_candidates == 1
    assert result.vector_candidates == len(list_chunks(session, a))
    email = by_id(result, "rec-email")
    assert (email.timestamp, email.timestamp_kind) == ("2026-04-10T09:15:00Z", "sent")
    assert by_id(result, "rec-chat").timestamp is None


def test_multiple_chunks_of_one_record_dedupe_to_one_result(session, cases, embedder):
    a, _ = cases
    chunks = list_chunks(session, a, "rec-long")
    assert len(chunks) > 3
    result = retrieve(session, a, "harbour", embedder)

    assert ids(result).count("rec-long") == 1
    long = by_id(result, "rec-long")
    assert result.records[0] is long  # keyword + vector beats vector only
    assert long.matched_by == "both"
    assert len(long.excerpts) == 2  # the two chunks containing "harbour"
    for excerpt in long.excerpts:
        assert excerpt.text == LONG_A[excerpt.char_start : excerpt.char_end]
        assert "harbour" in excerpt.text
        assert len(excerpt.chunk_ids) == 1
    hit_chunks = {c.chunk_id for c in chunks if "harbour" in c.text}
    assert {e.chunk_ids[0] for e in long.excerpts} == hit_chunks
    assert long.excerpts[0].score >= long.excerpts[1].score

    config = RetrievalConfig(max_excerpts_per_record=1)
    one = retrieve(session, a, "harbour", embedder, config=config)
    assert len(by_id(one, "rec-long").excerpts) == 1


def test_overlapping_chunks_merge_into_one_exact_excerpt(session, cases, embedder):
    a, _ = cases
    first, second = list_chunks(session, a, "rec-long")[:2]
    shared = sorted(set(first.text.split()) & set(second.text.split()))
    assert shared, "consecutive chunks overlap"
    result = retrieve(session, a, shared[0], embedder)

    long = by_id(result, "rec-long")
    assert result.records[0] is long
    assert len(long.excerpts) == 1
    merged = long.excerpts[0]
    assert sorted(merged.chunk_ids) == sorted([first.chunk_id, second.chunk_id])
    assert (merged.char_start, merged.char_end) == (first.char_start, second.char_end)
    assert merged.text == LONG_A[first.char_start : second.char_end]


def test_case_b_records_never_appear_in_case_a(session, cases, embedder):
    a, b = cases
    # Perfect vector match for B's record, plus terms that exist in B (and some in A).
    question = "Where does the zeppelin dock by the warehouse?"
    embedder.queries[question] = EMAIL_B
    a_contents = {r.record_id: r.content for r in list_records(session, a)}
    a_chunks = {c.chunk_id for c in list_chunks(session, a)}

    result = retrieve(session, a, question, embedder)
    assert result.records
    for rec in result.records:
        assert rec.record_id in a_contents
        for excerpt in rec.excerpts:
            assert excerpt.text in a_contents[rec.record_id]
            assert set(excerpt.chunk_ids) <= a_chunks
        if rec.vector:
            assert rec.vector.chunk_id in a_chunks
            assert rec.vector.score > 0.5  # B's distance-0 vector is not visible from A
    # "rec-email" exists in both cases: A's content is returned, never B's.
    assert by_id(result, "rec-email").excerpts[0].text == EMAIL_A
    # Keyword: "zeppelin" and "warehouse" are only in B.
    assert result.keyword_candidates == 0
    assert retrieve(session, a, "zeppelin", embedder).keyword_candidates == 0

    b_result = retrieve(session, b, question, embedder)
    assert ids(b_result) == ["rec-email"]
    assert b_result.records[0].matched_by == "both"
    assert b_result.records[0].excerpts[0].text == EMAIL_B


def test_retrieval_is_read_only_and_unrelated_records_stay_indexed(session, cases, embedder):
    a, b = cases

    def snapshot():
        counts = tuple(
            session.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("evidence_records", "evidence_chunks", VEC_TABLE, FTS_TABLE)
        )
        return counts, [r.to_contract_json() for r in list_records(session, a)]

    before = snapshot()
    retrieve(session, a, "Who mentioned a meeting?", embedder, limit=1)
    retrieve(session, a, "harbour", embedder, excluded_record_ids={"rec-unrelated"})
    retrieve(session, b, "zeppelin", embedder)
    session.commit()
    assert snapshot() == before
    # The unrelated record is still found by its own terms.
    result = retrieve(session, a, "tomato soup", embedder)
    assert result.records[0].record_id == "rec-unrelated"
    assert result.records[0].matched_by == "both"


def test_evidence_window_record_cap(session, embedder):
    case = Case(title="Many")
    session.add(case)
    session.flush()
    records = [
        record(case.case_id, f"rec-{i:02d}", f"Report {i} about the ferry.") for i in range(25)
    ]
    store_records(session, case.case_id, records, embedder)
    session.commit()

    result = retrieve(session, case.case_id, "ferry", embedder)
    assert len(result.records) == 16  # default RETRIEVAL_MAX_RECORDS
    assert result.fused_records == 25
    assert (result.dropped_by_limit, result.dropped_by_budget) == (9, 0)
    assert all(r.matched_by == "both" for r in result.records)
    # The 9 records past the cap are listed in rank order, without content.
    assert [s.reason for s in result.skipped] == ["record_limit"] * 9
    assert [s.rank for s in result.skipped] == list(range(17, 26))
    assert [s.excerpt_chars for s in result.skipped] == [None] * 9
    scores = [r.score for r in result.records] + [s.score for s in result.skipped]
    assert scores == sorted(scores, reverse=True)
    assert {s.record_id for s in result.skipped}.isdisjoint(ids(result))
    assert {s.record_id for s in result.skipped} | set(ids(result)) == {
        r.record_id for r in records
    }
    assert result.budget_substituted is False

    assert len(retrieve(session, case.case_id, "ferry", embedder, limit=3).records) == 3
    config = RetrievalConfig(max_records=5)
    assert len(retrieve(session, case.case_id, "ferry", embedder, config=config).records) == 5
    with pytest.raises(ValueError):
        retrieve(session, case.case_id, "ferry", embedder, limit=0)


def test_evidence_window_character_budget(session, cases, embedder):
    a, _ = cases
    question = "Who mentioned a meeting?"
    embedder.queries[question] = CHAT_A
    full = retrieve(session, a, question, embedder)
    assert full.excerpt_chars == sum(len(e.text) for r in full.records for e in r.excerpts)

    budget = len(CHAT_A) + len(EMAIL_A)
    capped = retrieve(session, a, question, embedder, max_excerpt_chars=budget)
    total = sum(len(e.text) for r in capped.records for e in r.excerpts)
    assert total == capped.excerpt_chars <= budget
    assert capped.records[0].record_id == "rec-chat"
    assert capped.dropped_by_budget > 0
    assert len(capped.records) + capped.dropped_by_budget + capped.dropped_by_limit == len(
        full.records
    )
    assert not any(e.truncated for r in capped.records for e in r.excerpts)
    budget_skips = [s for s in capped.skipped if s.reason == "budget"]
    assert len(budget_skips) == capped.dropped_by_budget
    assert all(s.excerpt_chars and s.excerpt_chars > 0 for s in budget_skips)

    # A budget smaller than the best excerpt still returns it, cut to fit.
    tiny = retrieve(session, a, question, embedder, max_excerpt_chars=10)
    assert ids(tiny) == ["rec-chat"]
    excerpt = tiny.records[0].excerpts[0]
    assert (excerpt.text, excerpt.truncated, excerpt.char_end) == (CHAT_A[:10], True, 10)
    assert tiny.excerpt_chars == 10

    config = RetrievalConfig(max_excerpt_chars=budget)
    assert retrieve(session, a, question, embedder, config=config).excerpt_chars <= budget


def test_budget_skip_is_listed_and_lower_ranked_record_fills_in(session, embedder):
    case = Case(title="Budget")
    session.add(case)
    session.flush()
    big = "The ferry left. " + " ".join(f"filler{i:03d}" for i in range(140))
    assert 1000 < len(big) <= 1600  # one chunk
    contents = {
        "r-top": "Ferry ferry timetable.",
        "r-big": big,
        "r-mid": "The ferry was late.",
        "r-tail": "Unrelated note about lunch.",
    }
    store_records(
        session, case.case_id, [record(case.case_id, k, v) for k, v in contents.items()], embedder
    )
    session.commit()
    question = "ferry"
    embedder.queries[question] = contents["r-top"]  # r-top: vector rank 1 and keyword hit

    full = retrieve(session, case.case_id, question, embedder)
    order = ids(full)
    assert order[0] == "r-top" and order[-1] == "r-tail"  # tail has no keyword hit
    big_rank = order.index("r-big") + 1
    assert 1 < big_rank < len(order)

    budget = full.excerpt_chars - len(big)  # everything except r-big fits exactly
    capped = retrieve(session, case.case_id, question, embedder, max_excerpt_chars=budget)
    assert ids(capped) == [r for r in order if r != "r-big"]
    [skip] = capped.skipped
    assert skip.model_dump() == {
        "record_id": "r-big",
        "rank": big_rank,
        "score": pytest.approx(by_id(full, "r-big").score),
        "reason": "budget",
        "excerpt_chars": len(big),
    }
    assert capped.dropped_by_budget == 1
    assert capped.budget_substituted is True
    assert capped.excerpt_chars == budget

    # The skipped record was last in the window: nothing lower-ranked filled in.
    tail_only = retrieve(
        session, case.case_id, question, embedder, limit=big_rank,
        max_excerpt_chars=sum(len(contents[r]) for r in order[: big_rank - 1]),
    )
    assert [s.record_id for s in tail_only.skipped][0] == "r-big"
    assert tail_only.budget_substituted is False


@pytest.mark.parametrize(
    ("question", "content"),
    [
        ("who was delivering", "Alex delivered the parcel."),
        ("who was delivering", "Alex delivers parcels."),
        ("delivered", "Alex will deliver it."),
        ("saying", "Jordan says nothing."),
        ("meetings", "There may be a meeting."),
        ("CONNECTED", "We connect at noon."),
    ],
)
def test_keyword_search_matches_stemmed_variants(session, embedder, question, content):
    case = Case(title="Stems")
    session.add(case)
    session.flush()
    store_records(session, case.case_id, [record(case.case_id, "rec-1", content)], embedder)
    session.commit()
    hits = keyword_search(session, case.case_id, to_fts_query(question))
    assert [h.record_id for h in hits] == ["rec-1"]
    assert by_id(retrieve(session, case.case_id, question, embedder), "rec-1").keyword


def test_old_tokenizer_index_is_rebuilt_at_startup(tmp_path, caplog):
    db = f"sqlite:///{tmp_path / 'old.db'}"
    embedder = FakeEmbedder()
    engine = make_engine(db)
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        a, b = Case(title="A"), Case(title="B")
        session.add_all([a, b])
        session.flush()
        a_id, b_id = a.case_id, b.case_id
        store_records(session, a_id, [record(a_id, "rec-1", "Alex delivered it."),
                                      record(a_id, "rec-2", LONG_A)], embedder)
        store_records(session, b_id, [record(b_id, "rec-1", "Morgan delivers.")], embedder)
        session.commit()
    # Simulate a database created before porter stemming (BLA-12's tokenizer).
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE {FTS_TABLE}"))
        conn.execute(text(
            f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(text, chunk_id UNINDEXED, "
            "case_id UNINDEXED, record_id UNINDEXED, tokenize = 'unicode61 remove_diacritics 2')"
        ))
        conn.execute(text(
            f"INSERT INTO {FTS_TABLE} (text, chunk_id, case_id, record_id) "
            "SELECT text, chunk_id, case_id, record_id FROM evidence_chunks"
        ))
    with make_session_factory(engine)() as session:
        assert keyword_index_tokenizer(session) == "unicode61 remove_diacritics 2"
        assert keyword_search(session, a_id, '"delivering"') == []  # no stemming yet
    engine.dispose()

    engine = make_engine(db)
    try:
        with caplog.at_level("WARNING", logger="app.search_index"):
            init_db(engine, embedder.dim)
        assert "tokenizer changed" in caplog.text
        with make_session_factory(engine)() as session:
            assert keyword_index_tokenizer(session) == FTS_TOKENIZE
            chunks = session.execute(text("SELECT count(*) FROM evidence_chunks")).scalar_one()
            assert session.execute(text(f"SELECT count(*) FROM {FTS_TABLE}")).scalar_one() == (
                chunks
            )
            assert [h.record_id for h in keyword_search(session, a_id, '"delivering"')] == [
                "rec-1"
            ]
            assert [h.record_id for h in keyword_search(session, b_id, '"delivering"')] == [
                "rec-1"
            ]
            assert keyword_search(session, a_id, '"harbour"')[0].record_id == "rec-2"
            # The delete trigger still keeps the rebuilt index in sync.
            session.execute(text("DELETE FROM evidence_records WHERE record_id = 'rec-2'"))
            session.commit()
            assert keyword_search(session, a_id, '"harbour"') == []
        caplog.clear()
        init_db(engine, embedder.dim)  # already current: no rebuild, no duplicate rows
        assert "tokenizer changed" not in caplog.text
        with make_session_factory(engine)() as session:
            assert len(keyword_search(session, a_id, '"delivering"')) == 1
    finally:
        engine.dispose()


def test_excluded_records_are_never_returned(session, cases, embedder):
    a, _ = cases
    question = "Who mentioned a meeting?"
    embedder.queries[question] = CHAT_A
    result = retrieve(session, a, question, embedder, excluded_record_ids=["rec-chat"])
    assert "rec-chat" not in ids(result)
    assert result.keyword_candidates == 0  # the only keyword hit was excluded
    assert result.excluded_record_ids == ["rec-chat"]
    assert len(result.records) == 4
    # Nothing is removed from the corpus.
    assert "rec-chat" in ids(retrieve(session, a, question, embedder))


def test_unknown_case_and_wrong_embedder(session, cases, embedder):
    a, _ = cases
    with pytest.raises(CaseNotFoundError):
        retrieve(session, "case_missing", "meeting", embedder)
    with pytest.raises(EmbeddingIndexMismatchError):
        retrieve(session, a, "meeting", FakeEmbedder(dim=8))


@pytest.mark.parametrize("question", ["", "   \n\t", "???", "?!... --- ''", '"" ** () :', "🔥🔥"])
def test_empty_or_punctuation_only_question_returns_nothing(session, cases, embedder, question):
    a, _ = cases
    result = retrieve(session, a, question, embedder)
    assert result.records == []
    assert result.fts_query is None
    assert (result.vector_candidates, result.keyword_candidates, result.fused_records) == (0, 0, 0)
    assert embedder.query_calls == []  # no embedding call for an empty question


# --- Free text -> FTS5 query ------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("what did John say?", '"john" OR "say"'),
        ("it's", '"it s"'),
        ("a-b", '"a b"'),
        ("rec-0042", '"rec 0042" OR "rec" OR "0042"'),
        ('"John" said', '"john" OR "said"'),
        ("John*", '"john"'),
        ("from:John", '"from john" OR "john"'),
        ("(John OR Jordan)", '"john" OR "jordan"'),
        ("NEAR(John Jordan)", '"near john" OR "near" OR "john" OR "jordan"'),
        ("John AND Jordan NOT Casey", '"john" OR "jordan" OR "casey"'),
        ("AND", '"and"'),  # only stopwords: keep them rather than search nothing
        ("who is it?", '"who" OR "is" OR "it"'),
        ("Café Zoë", '"café" OR "zoë"'),
        ("ＪＯＨＮ", '"john"'),  # NFKC folds full-width letters
        ("snake_case", '"snake case" OR "snake" OR "case"'),
        ("John john JOHN", '"john"'),
        ("", None),
        ("   ", None),
        ("?!*", None),
    ],
)
def test_to_fts_query(question, expected) -> None:
    assert to_fts_query(question) == expected


def test_to_fts_query_caps_terms() -> None:
    query = to_fts_query(" ".join(f"word{i}" for i in range(100)))
    assert query.count(" OR ") == MAX_FTS_TERMS - 1


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("what did John say?", "rec-note"),
        ("it's", "rec-note"),
        ("a-b", "rec-note"),
        ('"John"', "rec-note"),
        ("John*", "rec-note"),
        ("from:John", "rec-note"),
        ("(John", "rec-note"),
        ('John"', "rec-note"),
        ("NEAR(John, Jordan)", "rec-note"),
        ("John AND", "rec-note"),
        ("OR John", "rec-note"),
        ("cafe", "rec-note"),  # diacritics removed by the tokenizer
        ("Café", "rec-note"),
        ("zoe", "rec-note"),
        ("10:30", "rec-note"),
        ("Jordan's meeting", "rec-chat"),
        ("delivering; document!", "rec-email"),
    ],
)
def test_quoted_questions_search_without_errors(session, cases, embedder, question, expected):
    a, _ = cases
    hits = keyword_search(session, a, to_fts_query(question))
    assert hits and hits[0].record_id == expected
    result = retrieve(session, a, question, embedder)
    assert by_id(result, expected).keyword is not None


@pytest.mark.parametrize("raw", ["what did John say?", "it's", "a-b", "(John", "John AND"])
def test_raw_questions_are_fts5_syntax_errors(session, cases, raw):
    """Why the quoting helper exists: ``keyword_search`` takes raw FTS5 syntax."""
    a, _ = cases
    with pytest.raises(OperationalError):
        keyword_search(session, a, raw)
