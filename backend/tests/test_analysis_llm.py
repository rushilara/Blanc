"""BLA-15: one structured LLM call per analysis. Offline: fake LLM and a mocked Gemini client."""

import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jsonschema
import pytest
from _paths import RAW
from google.genai import errors as genai_errors
from google.genai import types
from sqlalchemy.orm import Session

from app.analysis_llm import (
    DEFAULT_TEMPERATURE,
    EMPTY_OUTPUT,
    EXCERPT_SEPARATOR,
    AnalysisLLM,
    AnalysisLLMError,
    AnalysisOutput,
    AnalysisOutputError,
    FakeAnalysisLLM,
    GeminiAnalysisLLM,
    build_analysis_messages,
    gemini_response_schema,
    make_analysis_llm,
    parse_analysis_output,
    run_analysis,
)
from app.config import Settings
from app.contracts import AnalysisRevision, EvidenceRecord
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.models import Case
from app.retrieval import Excerpt, RetrievalResult, RetrievedRecord, retrieve
from evidence_prompt import SYSTEM_PROMPT

OUTPUT_FIELDS = ("answer", "claims", "graph", "timeline_events", "unresolved_questions")


def llm_output(**changes: Any) -> dict[str, Any]:
    """The LLM-authored part of the synthetic revision fixture."""
    return {**{k: deepcopy(RAW["revision"][k]) for k in OUTPUT_FIELDS}, **changes}


def excerpt(text: str, start: int) -> Excerpt:
    return Excerpt(text=text, char_start=start, char_end=start + len(text), chunk_ids=["c"],
                   score=0.1)


def retrieved(record_id: str, *excerpts: Excerpt, **extra: Any) -> RetrievedRecord:
    return RetrievedRecord(
        record_id=record_id, source_file=f"{record_id}.txt", source_locator="line 1",
        score=0.5, matched_by="both", excerpts=list(excerpts), **extra,
    )


def retrieval(*records: RetrievedRecord, excluded: list[str] | None = None) -> RetrievalResult:
    return RetrievalResult(
        case_id="synthetic-demo", question="How are Alex and Casey connected?", fts_query="x",
        records=list(records), vector_candidates=0, keyword_candidates=0, fused_records=0,
        dropped_by_limit=0, dropped_by_budget=0, excerpt_chars=0,
        excluded_record_ids=excluded or [],
    )


def payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    assert [m["role"] for m in messages] == ["system", "user"]
    return json.loads(messages[1]["content"])


RETRIEVAL = retrieval(
    retrieved("rec-01", excerpt("Alex emails Jordan.", 0), timestamp="2026-04-10T09:15:00Z",
              timestamp_kind="sent"),
    retrieved("rec-02", excerpt("Jordan tells Casey.", 0)),
)


# --- run_analysis ------------------------------------------------------------------------


def test_one_call_returns_all_six_fields_with_evidence() -> None:
    llm = FakeAnalysisLLM([llm_output()])
    output = run_analysis(llm, RETRIEVAL)
    assert len(llm.calls) == 1
    assert isinstance(output, AnalysisOutput)
    assert output.answer.startswith("An indirect communication link")
    assert len(output.claims) == 2 and output.unresolved_questions
    assert len(output.graph.nodes) == 3 and len(output.graph.edges) == 2
    assert len(output.timeline_events) == 2
    for item in [*output.claims, *output.graph.edges, *output.timeline_events]:
        assert item.evidence_ids


def test_messages_carry_question_and_retrieved_excerpts() -> None:
    llm = FakeAnalysisLLM()
    run_analysis(llm, RETRIEVAL, prior_analysis="Earlier summary.")
    (messages,) = llm.calls
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    data = payload(messages)
    assert data["case_id"] == "synthetic-demo"
    assert data["investigator_question"] == RETRIEVAL.question
    assert data["context"] == {"label": "prior analysis — not evidence",
                               "text": "Earlier summary."}
    assert data["source_records"] == [
        {"record_id": "rec-01", "source_file": "rec-01.txt", "source_locator": "line 1",
         "content": "Alex emails Jordan.", "timestamp": "2026-04-10T09:15:00Z",
         "timestamp_kind": "sent"},
        {"record_id": "rec-02", "source_file": "rec-02.txt", "source_locator": "line 1",
         "content": "Jordan tells Casey."},
    ]


def test_multiple_excerpts_join_in_document_order() -> None:
    # Retrieval lists excerpts best first; the prompt shows them in document order.
    result = retrieval(retrieved("rec-01", excerpt("later part", 500), excerpt("early part", 10)))
    record = payload(build_analysis_messages(result))["source_records"][0]
    assert record["content"] == f"early part{EXCERPT_SEPARATOR}later part"


@pytest.mark.parametrize("where", ["argument", "retrieval"])
def test_excluded_records_never_reach_the_messages(where: str) -> None:
    secret = retrieved("rec-x", excerpt("EXCLUDED_SECRET", 0))
    if where == "argument":
        result, excluded = retrieval(*RETRIEVAL.records, secret), ["rec-x"]
    else:
        result, excluded = retrieval(*RETRIEVAL.records, secret, excluded=["rec-x"]), []
    llm = FakeAnalysisLLM()
    run_analysis(llm, result, excluded_record_ids=excluded)
    (messages,) = llm.calls
    assert all("EXCLUDED_SECRET" not in m["content"] and "rec-x" not in m["content"]
               for m in messages)
    assert [r["record_id"] for r in payload(messages)["source_records"]] == ["rec-01", "rec-02"]


def test_no_retrieved_records_still_makes_one_call() -> None:
    llm = FakeAnalysisLLM()
    assert run_analysis(llm, retrieval()) == EMPTY_OUTPUT
    assert len(llm.calls) == 1 and payload(llm.calls[0])["source_records"] == []


def test_node_and_edge_types_come_from_the_model() -> None:
    # Nothing about entities or relationships is hardcoded: whatever the model returns
    # (within the contract) passes through unchanged.
    graph = {
        "nodes": [
            {"id": "n-ship", "label": "MV Example", "type": "other"},
            {"id": "n-port", "label": "Port Z", "type": "location"},
        ],
        "edges": [{"id": "e-1", "source": "n-ship", "target": "n-port",
                   "type": "docked_at_reportedly", "evidence_ids": ["rec-02"],
                   "status": "inferred"}],
    }
    output = run_analysis(FakeAnalysisLLM([llm_output(graph=graph)]), RETRIEVAL)
    assert [n.label for n in output.graph.nodes] == ["MV Example", "Port Z"]
    assert output.graph.edges[0].type == "docked_at_reportedly"


def test_session_adds_stored_metadata(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'analysis.db'}")
    embedder = FakeEmbedder()
    init_db(engine, embedder.dim)
    try:
        with make_session_factory(engine)() as session:
            case_id = _store_fixture_records(session, embedder)
            result = retrieve(session, case_id, "Alex emails Jordan", embedder)
            llm = FakeAnalysisLLM()
            run_analysis(llm, result, session=session)
    finally:
        engine.dispose()
    records = {r["record_id"]: r for r in payload(llm.calls[0])["source_records"]}
    assert records["rec-01"]["metadata"] == {"sender": "Alex", "recipient": "Jordan"}
    assert records["rec-02"]["metadata"] == {}
    assert records["rec-01"]["content"] == RAW["records"][0]["content"]


def _store_fixture_records(session: Session, embedder: FakeEmbedder) -> str:
    case = Case(title="Analysis")
    session.add(case)
    session.flush()
    records = [EvidenceRecord.model_validate(dict(r, case_id=case.case_id))
               for r in RAW["records"]]
    store_records(session, case.case_id, records, embedder)
    session.commit()
    return case.case_id


def test_output_merges_into_a_valid_revision() -> None:
    output = run_analysis(FakeAnalysisLLM([llm_output()]), RETRIEVAL)
    meta = {k: v for k, v in RAW["revision"].items() if k not in OUTPUT_FIELDS}
    revision = AnalysisRevision.model_validate({**meta, **output.to_contract_json()})
    assert revision.claims == output.claims


# --- Output parsing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("   ", "empty"),
        ("```json\n{}\n```", "not valid JSON"),
        ('{"answer": "cut off', "not valid JSON"),
        ("[]", "must be a JSON object"),
    ],
)
def test_non_json_output_raises(text: str, expected: str) -> None:
    with pytest.raises(AnalysisOutputError) as info:
        run_analysis(FakeAnalysisLLM([text]), RETRIEVAL)
    assert expected in str(info.value)
    assert info.value.raw == text


def test_parse_none_is_empty() -> None:
    with pytest.raises(AnalysisOutputError, match="empty"):
        parse_analysis_output(None)


def _broken_output() -> dict[str, Any]:
    out = llm_output()
    out["claims"][0]["evidence_ids"] = []
    out["claims"][1]["status"] = "verified"
    del out["graph"]["edges"][0]["evidence_ids"]
    out["timeline_events"][0]["status"] = "unresolved"
    out["graph"]["edges"][1]["description"] = None
    out["extra"] = "not in the contract"
    del out["unresolved_questions"]
    return out


def test_schema_violations_raise_with_readable_paths() -> None:
    with pytest.raises(AnalysisOutputError) as info:
        run_analysis(FakeAnalysisLLM([_broken_output()]), RETRIEVAL)
    errors = info.value.errors
    joined = "\n".join(errors)
    for path in ("claims[0].evidence_ids", "claims[1].status", "graph.edges[0].evidence_ids",
                 "timeline_events[0].status", "graph.edges[1].description", "extra",
                 "unresolved_questions"):
        assert path in joined, path
    assert json.loads(info.value.raw)["extra"] == "not in the contract"
    assert isinstance(info.value, AnalysisLLMError)


def test_strict_types_no_coercion() -> None:
    with pytest.raises(AnalysisOutputError, match="answer"):
        parse_analysis_output(json.dumps(llm_output(answer=42)))


def test_fake_llm_scripted_responses_then_repeats_last() -> None:
    llm = FakeAnalysisLLM(["not json", EMPTY_OUTPUT])
    with pytest.raises(AnalysisOutputError):
        llm.generate([{"role": "user", "content": "q"}])
    assert llm.generate([{"role": "user", "content": "q"}]) is EMPTY_OUTPUT
    assert llm.generate([{"role": "user", "content": "q"}]) is EMPTY_OUTPUT
    assert isinstance(llm, AnalysisLLM)


# --- Gemini response schema --------------------------------------------------------------

_ALLOWED_KEYWORDS = {"type", "enum", "items", "minItems", "properties", "required", "anyOf"}


def _walk(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield schema
    for sub in [*schema.get("properties", {}).values(), *schema.get("anyOf", [])]:
        yield from _walk(sub)
    if "items" in schema:
        yield from _walk(schema["items"])


def test_gemini_schema_uses_only_supported_keywords() -> None:
    schema = gemini_response_schema()
    for node in _walk(schema):
        assert set(node) <= _ALLOWED_KEYWORDS, set(node) - _ALLOWED_KEYWORDS
    assert set(schema["required"]) == set(OUTPUT_FIELDS)


def test_gemini_schema_matches_contract_shapes() -> None:
    schema = gemini_response_schema()
    props = schema["properties"]
    edge = props["graph"]["properties"]["edges"]["items"]
    event = props["timeline_events"]["items"]
    node = props["graph"]["properties"]["nodes"]["items"]
    claim = props["claims"]["items"]
    for item in (claim, edge, event):
        assert "evidence_ids" in item["required"]
        assert item["properties"]["evidence_ids"]["minItems"] == 1
    assert claim["properties"]["status"]["enum"] == [
        "observed", "reported", "inferred", "unresolved"]
    assert edge["properties"]["status"]["enum"] == ["observed", "reported", "inferred"]
    assert edge["properties"]["type"] == {"type": "string"}  # free-form relationship type
    assert "other" in node["properties"]["type"]["enum"]
    # Omittable-but-not-null fields lose their null branch; required nullable ones keep it.
    assert edge["properties"]["description"] == {"type": "string"}
    assert node["properties"]["evidence_ids"]["type"] == "array"
    assert event["properties"]["timestamp"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}


def test_gemini_schema_accepts_fixture_output() -> None:
    jsonschema.validate(llm_output(), gemini_response_schema())
    jsonschema.validate(llm_output(), _contract_output_schema())


def _contract_output_schema() -> dict[str, Any]:
    schema = json.loads((Path(__file__).resolve().parents[2] / "contracts" /
                         "analysis_revision.schema.json").read_text())
    return {"type": "object", "properties": {k: schema["properties"][k] for k in OUTPUT_FIELDS},
            "required": list(OUTPUT_FIELDS), "additionalProperties": False}


def test_output_model_fields_match_revision() -> None:
    assert set(AnalysisOutput.model_fields) == set(OUTPUT_FIELDS)
    for name, info in AnalysisOutput.model_fields.items():
        assert info.annotation == AnalysisRevision.model_fields[name].annotation


# --- GeminiAnalysisLLM -------------------------------------------------------------------


class FakeModels:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def gemini(response: Any = None, error: Exception | None = None) -> tuple[GeminiAnalysisLLM,
                                                                          FakeModels]:
    models = FakeModels(response, error)
    return GeminiAnalysisLLM("key", "gemini-test", client=SimpleNamespace(models=models)), models


def text_response(text: str | None, finish_reason: str = "STOP") -> SimpleNamespace:
    reason = SimpleNamespace(name=finish_reason)
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=reason)])


def test_gemini_builds_structured_request() -> None:
    llm, models = gemini(text_response(json.dumps(llm_output())))
    output = run_analysis(llm, RETRIEVAL)
    assert output.claims[0].id == "claim-1"
    (call,) = models.calls
    assert call["model"] == "gemini-test"
    config = call["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == gemini_response_schema()
    assert config.response_schema is None
    assert config.system_instruction == SYSTEM_PROMPT
    assert config.temperature == DEFAULT_TEMPERATURE == 1.0
    (content,) = call["contents"]
    assert content.role == "user"
    assert payload(build_analysis_messages(RETRIEVAL)) == json.loads(content.parts[0].text)
    assert SYSTEM_PROMPT not in content.parts[0].text


def test_gemini_maps_assistant_turns_to_model_role() -> None:
    llm, models = gemini(text_response(json.dumps(llm_output())))
    llm.generate([
        {"role": "system", "content": "sys"}, {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"}, {"role": "user", "content": "u2"},
    ])
    contents = models.calls[0]["contents"]
    assert [(c.role, c.parts[0].text) for c in contents] == [
        ("user", "u1"), ("model", "a1"), ("user", "u2")]


@pytest.mark.parametrize(
    "messages",
    [[{"role": "system", "content": "only system"}], [{"role": "tool", "content": "x"}]],
)
def test_gemini_rejects_bad_messages(messages: list[dict[str, str]]) -> None:
    llm, models = gemini(text_response("{}"))
    with pytest.raises(ValueError):
        llm.generate(messages)
    assert models.calls == []


def test_gemini_invalid_output_raises_output_error() -> None:
    llm, _ = gemini(text_response('{"answer": "x"}'))
    with pytest.raises(AnalysisOutputError) as info:
        run_analysis(llm, RETRIEVAL)
    assert "claims: Field required" in info.value.errors


def test_gemini_empty_response_reports_finish_reason() -> None:
    llm, _ = gemini(text_response(None, "MAX_TOKENS"))
    with pytest.raises(AnalysisOutputError, match="MAX_TOKENS"):
        run_analysis(llm, RETRIEVAL)
    llm, _ = gemini(SimpleNamespace(text=None, candidates=None))
    with pytest.raises(AnalysisOutputError, match="unknown"):
        run_analysis(llm, RETRIEVAL)


@pytest.mark.parametrize(
    "error",
    [
        genai_errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}),
        genai_errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}}),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        ConnectionResetError("reset by peer"),
    ],
)
def test_gemini_provider_failure_is_wrapped(error: Exception) -> None:
    llm, _ = gemini(error=error)
    with pytest.raises(AnalysisLLMError, match="Gemini analysis request failed") as info:
        run_analysis(llm, RETRIEVAL)
    assert not isinstance(info.value, AnalysisOutputError)
    assert info.value.__cause__ is error


@pytest.mark.parametrize("error", [TypeError("bad argument"), KeyError("missing"),
                                   RuntimeError("bug")])
def test_gemini_programming_errors_are_not_wrapped(error: Exception) -> None:
    llm, _ = gemini(error=error)
    with pytest.raises(type(error)) as info:
        run_analysis(llm, RETRIEVAL)
    assert info.value is error
    assert not isinstance(info.value, AnalysisLLMError)


def test_missing_key_errors_at_call_time_not_construction() -> None:
    llm = make_analysis_llm(Settings(_env_file=None, GEMINI_API_KEY=None))
    assert isinstance(llm, GeminiAnalysisLLM)
    assert llm.model == Settings(_env_file=None).GEMINI_MODEL
    with pytest.raises(AnalysisLLMError, match="GEMINI_API_KEY is not set"):
        run_analysis(llm, RETRIEVAL)


def test_client_created_lazily_with_key() -> None:
    llm = make_analysis_llm(Settings(_env_file=None, GEMINI_API_KEY="test-key",
                                     GEMINI_MODEL="gemini-x"))
    assert llm.model == "gemini-x" and llm._client is None
    client = llm._get_client()
    assert client is llm._get_client()  # no network: constructing the client is offline


def test_app_startup_exposes_lazy_analysis_llm(client) -> None:
    assert isinstance(client.app.state.analysis_llm, GeminiAnalysisLLM)
