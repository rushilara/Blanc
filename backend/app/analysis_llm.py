"""One structured LLM call per analysis (BLA-15): retrieved evidence in, ``AnalysisOutput`` out.

``run_analysis`` turns a ``RetrievalResult`` into the source records expected by
``evidence_prompt.build_evidence_messages`` (which drops excluded records again as defense in
depth), makes exactly one ``AnalysisLLM.generate`` call and returns the parsed output.

``AnalysisOutput`` is the LLM-authored part of an ``AnalysisRevision``: ``answer``, ``claims``,
``graph`` (nodes, edges), ``timeline_events`` and ``unresolved_questions``. It reuses the item
models from ``app.contracts``, so it cannot drift from ``contracts/analysis_revision.schema.json``.
Revision metadata (IDs, retrieved/excluded sets, timestamps) is assigned by the server, and
cross-reference checks (cited IDs were retrieved and not excluded, edge endpoints exist) live
in ``app.validation``; this module only guarantees schema-shaped output or an
``AnalysisOutputError``.

Gemini is asked for JSON constrained by ``gemini_response_schema()``, a reduced form of the
Pydantic schema (see its docstring). The response is then validated strictly with
``AnalysisOutput``, so anything the reduced schema cannot express is still enforced.

Node ``type`` is the contract's fixed category enum; node labels, edge ``type`` and every ID
come from the model at runtime, never from case-specific code.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis_media import MediaPart, media_inputs
from app.config import Settings
from app.contracts import Claim, ContractModel, Graph, TimelineEvent
from app.models import EvidenceRecordRow
from app.retrieval import RetrievalResult, RetrievedRecord
from evidence_prompt import build_evidence_messages

# ``content`` is a string, or (BLA-71, when images/PDFs are sent) a list of parts:
# ``{"type": "text", "text": ...}`` and ``{"type": "media", "record_id", "mime_type",
# "data": bytes}``; see ``evidence_prompt.build_evidence_messages``.
Message = Mapping[str, Any]

# Placed between two excerpts of the same record in the prompt ``content``: the text between
# them was not retrieved, so the model must not read the excerpts as one continuous passage.
EXCERPT_SEPARATOR = "\n[...]\n"


class AnalysisLLMError(RuntimeError):
    """The LLM provider could not be called or failed (missing key, network, API error)."""


class AnalysisOutputError(AnalysisLLMError):
    """The model answered, but not with valid ``AnalysisOutput`` JSON.

    ``errors`` are short, readable messages (one per problem, with the JSON path), meant to
    be fed back to the model in a retry prompt; ``raw`` is the model's text as received.
    """

    def __init__(self, errors: Sequence[str], raw: str | None) -> None:
        self.errors = list(errors)
        self.raw = raw
        super().__init__("LLM output is not a valid analysis: " + "; ".join(self.errors))


class AnalysisOutput(ContractModel):
    """The LLM-produced fields of an ``AnalysisRevision``, validated with the contract models.

    To build a revision, merge ``to_contract_json()`` (not ``model_dump()``, which writes
    omitted optional fields as nulls the contract rejects) with the server-assigned fields.
    """

    answer: str
    claims: list[Claim]
    graph: Graph
    timeline_events: list[TimelineEvent]
    unresolved_questions: list[str]


def parse_analysis_output(text: str | None) -> AnalysisOutput:
    """Parse model text into ``AnalysisOutput``; raise ``AnalysisOutputError`` if it is not
    a single JSON object that validates."""
    if text is None or not text.strip():
        raise AnalysisOutputError(["the response was empty; return one JSON object"], text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalysisOutputError(
            [f"the response is not valid JSON ({exc.msg} at line {exc.lineno} column "
             f"{exc.colno}); return one JSON object without Markdown fences"],
            text,
        ) from exc
    if not isinstance(data, dict):
        raise AnalysisOutputError(
            [f"the response must be a JSON object, got {type(data).__name__}"], text
        )
    try:
        return AnalysisOutput.model_validate(data)
    except ValidationError as exc:
        raise AnalysisOutputError(_readable_errors(exc), text) from exc


def _readable_errors(exc: ValidationError) -> list[str]:
    errors = []
    for error in exc.errors(include_url=False):
        path = "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error["loc"]
        ).lstrip(".")
        errors.append(f"{path or '<root>'}: {error['msg']}")
    return errors


# --- Gemini-compatible response schema ---------------------------------------------------

# Keywords kept in the schema sent to Gemini. Dropped: ``$defs``/``$ref`` (inlined),
# ``title``/``default``/``description`` (Pydantic fills these from class names and
# docstrings), ``minLength``/``uniqueItems`` (not in Gemini's supported subset) and
# ``additionalProperties`` (google-genai rejects it on the Developer API for
# ``response_schema``; constrained decoding only emits declared properties anyway).
# Pydantic still enforces all of them.
_GEMINI_KEYWORDS = frozenset(
    {"type", "enum", "items", "minItems", "properties", "required", "anyOf"}
)


def gemini_response_schema() -> dict[str, Any]:
    """JSON Schema for ``response_json_schema``, derived from ``AnalysisOutput``.

    Besides inlining and dropping unsupported keywords, fields that the contract lets you
    omit but not set to null (an edge ``description``, a node's ``evidence_ids``, ...) lose
    their ``null`` branch, so the model omits them instead of writing ``null``. Fields that
    are required but nullable (an event ``timestamp``) keep ``anyOf: [string, null]``.
    """
    schema = AnalysisOutput.model_json_schema()
    return _reduce(schema, schema.get("$defs", {}))


def _reduce(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in node:
        return _reduce(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]), defs)
    out = {key: value for key, value in node.items() if key in _GEMINI_KEYWORDS}
    if "items" in out:
        out["items"] = _reduce(out["items"], defs)
    if "anyOf" in out:
        out["anyOf"] = [_reduce(branch, defs) for branch in out["anyOf"]]
    if "properties" in out:
        required = set(out.get("required", ()))
        properties = {}
        for name, prop in out["properties"].items():
            reduced = _reduce(prop, defs)
            if name not in required:
                reduced = _drop_null_branch(reduced)
            properties[name] = reduced
        out["properties"] = properties
    return out


def _drop_null_branch(node: dict[str, Any]) -> dict[str, Any]:
    branches = [b for b in node.get("anyOf", ()) if b.get("type") != "null"]
    if "anyOf" not in node or len(branches) != 1:
        return node
    rest = {key: value for key, value in node.items() if key != "anyOf"}
    return {**branches[0], **rest}


# --- LLM implementations -----------------------------------------------------------------


@runtime_checkable
class AnalysisLLM(Protocol):
    """Turns prompt messages (``{"role": "system" | "user" | "assistant", "content": ...}``)
    into a parsed ``AnalysisOutput``. One ``generate`` call is one model request. ``content``
    may be a list of text and media parts (see ``Message``); an implementation must send
    every part in order or fail, never drop one."""

    model: str

    def generate(self, messages: Sequence[Message]) -> AnalysisOutput: ...


# Google recommends 1.0 for Gemini 3 models; lower values can cause looping or repetitive
# output. Structure and grounding are enforced by the response schema and server validation.
DEFAULT_TEMPERATURE = 1.0


class GeminiAnalysisLLM:
    """Structured analysis from the Gemini API via ``google-genai``.

    System messages become ``system_instruction``; user and assistant messages become
    ``user`` and ``model`` turns. A list ``content`` becomes one part per item, in order:
    text parts as text and media parts as inline bytes (``types.Part.from_bytes``, BLA-71;
    the Files API is not used, so no file is uploaded to provider file storage). The
    request sets ``response_mime_type="application/json"``
    and ``response_json_schema=gemini_response_schema()``, and the text is parsed with
    ``parse_analysis_output``. Rate limits (429) and transient server errors are retried by
    the SDK (``max_attempts`` per request); invalid output is not retried here. The client is
    created lazily, so constructing this without an API key is fine until it is used.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_attempts: int = 5,
        client: Any = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_attempts = max_attempts
        self._api_key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise AnalysisLLMError("GEMINI_API_KEY is not set; cannot call the Gemini LLM")
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

    def generate(self, messages: Sequence[Message]) -> AnalysisOutput:
        import httpx
        from google.genai import errors, types

        client = self._get_client()
        system = [m["content"] for m in messages if m["role"] == "system"]
        if not all(isinstance(text, str) for text in system):
            raise ValueError("system messages must be text")
        contents = []
        for message in messages:
            if message["role"] == "system":
                continue
            if message["role"] not in ("user", "assistant"):
                raise ValueError(f"unsupported message role {message['role']!r}")
            contents.append(
                types.Content(
                    role="model" if message["role"] == "assistant" else "user",
                    parts=_gemini_parts(types, message["content"]),
                )
            )
        if not contents:
            raise ValueError("messages need at least one user message")
        config = types.GenerateContentConfig(
            system_instruction="\n\n".join(system) or None,
            temperature=self.temperature,
            response_mime_type="application/json",
            response_json_schema=gemini_response_schema(),
        )
        try:
            response = client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        # Only provider and transport failures; bugs in our own request building must surface
        # as themselves, not as an outage.
        except (errors.APIError, httpx.TransportError, OSError) as exc:
            raise AnalysisLLMError(f"Gemini analysis request failed: {exc}") from exc
        text = response.text
        if not text:
            raise AnalysisOutputError(
                [f"the response had no text (finish reason: {_finish_reason(response)})"], text
            )
        return parse_analysis_output(text)


def _gemini_parts(types: Any, content: Any) -> list[Any]:
    if isinstance(content, str):
        return [types.Part(text=content)]
    parts = []
    for part in content:
        if part.get("type") == "text":
            parts.append(types.Part(text=part["text"]))
        elif part.get("type") == "media":
            parts.append(types.Part.from_bytes(data=part["data"], mime_type=part["mime_type"]))
        else:
            raise ValueError(f"unsupported prompt part type {part.get('type')!r}")
    return parts


def _finish_reason(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return str(getattr(reason, "name", reason) or "unknown")


@dataclass
class FakeAnalysisLLM:
    """Offline LLM for tests. Each ``generate`` call pops the next scripted response, or
    repeats the last one; a response is an ``AnalysisOutput``, a dict, or raw text (parsed
    with ``parse_analysis_output``, so invalid text raises like the real model). Records the
    messages of every call, including any image/PDF parts (vision-capable: see
    ``media_parts``)."""

    responses: list[AnalysisOutput | dict[str, Any] | str] = field(
        default_factory=lambda: [EMPTY_OUTPUT]
    )
    model: str = "fake-analysis"
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    def generate(self, messages: Sequence[Message]) -> AnalysisOutput:
        self.calls.append([dict(m) for m in messages])
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, AnalysisOutput):
            return response
        return parse_analysis_output(
            response if isinstance(response, str) else json.dumps(response)
        )


def media_parts(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """The image/PDF parts in ``messages``, in order (for tests and logging-free checks)."""
    return [
        part
        for message in messages
        if not isinstance(message["content"], str)
        for part in message["content"]
        if part.get("type") == "media"
    ]


EMPTY_OUTPUT = AnalysisOutput(
    answer="", claims=[], graph=Graph(nodes=[], edges=[]), timeline_events=[],
    unresolved_questions=[],
)


def make_analysis_llm(settings: Settings) -> GeminiAnalysisLLM:
    """The production analysis LLM for these settings (lazy: no network or key needed yet)."""
    key = settings.GEMINI_API_KEY.get_secret_value() if settings.GEMINI_API_KEY else None
    return GeminiAnalysisLLM(key, settings.GEMINI_MODEL)


# --- Running an analysis -----------------------------------------------------------------


def record_metadata(
    session: Session, case_id: str, record_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Stored ``metadata`` of these records (sender, recipient, ...), by record ID. Records
    without a metadata object are left out."""
    ids = list(record_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(EvidenceRecordRow.record_id, EvidenceRecordRow.record_metadata).where(
            EvidenceRecordRow.case_id == case_id, EvidenceRecordRow.record_id.in_(ids)
        )
    )
    return {record_id: metadata for record_id, metadata in rows if metadata is not None}


def prompt_record(
    case_id: str, record: RetrievedRecord, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One retrieved record as a ``build_evidence_messages`` source record.

    ``content`` is the record's retrieved excerpts in document order, joined with
    ``EXCERPT_SEPARATOR``; the model sees only what retrieval selected, never the full
    record. ``timestamp``/``timestamp_kind``/``metadata`` are included when present. An
    image/PDF record has no excerpts (empty ``content``) and carries ``modality`` and
    ``content_type``; its file is attached as a media part (BLA-71).
    """
    excerpts = sorted(record.excerpts, key=lambda e: e.char_start)
    source: dict[str, Any] = {
        "record_id": record.record_id,
        "case_id": case_id,
        "source_file": record.source_file,
        "source_locator": record.source_locator,
        "content": EXCERPT_SEPARATOR.join(e.text for e in excerpts),
    }
    if record.modality != "text":
        source["modality"] = record.modality
        if record.content_type is not None:
            source["content_type"] = record.content_type
    if record.timestamp is not None:
        source["timestamp"] = record.timestamp
    if record.timestamp_kind is not None:
        source["timestamp_kind"] = record.timestamp_kind
    if metadata is not None:
        source["metadata"] = metadata
    return source


def build_analysis_messages(
    retrieval: RetrievalResult,
    *,
    excluded_record_ids: Iterable[str] = (),
    prior_analysis: str | None = None,
    metadata_by_id: Mapping[str, dict[str, Any]] | None = None,
    media: Mapping[str, MediaPart] | None = None,
) -> list[dict[str, Any]]:
    """Prompt messages for ``retrieval``'s question and records. Records in
    ``excluded_record_ids`` or ``retrieval.excluded_record_ids`` are dropped again by
    ``build_evidence_messages``, and so is their media.

    ``media`` (BLA-71, from ``app.analysis_media.select_media``) holds the image/PDF parts
    to send inline, by record ID; each follows its record's tag in the prompt. An image/PDF
    record without a part is left out of the prompt entirely, so the model is never shown a
    record whose file it cannot see (``sent_record_ids`` gives the records actually sent).
    """
    metadata_by_id = metadata_by_id or {}
    media = media or {}
    sent = sent_record_ids(retrieval, media)
    records = [
        prompt_record(retrieval.case_id, r, metadata_by_id.get(r.record_id))
        for r in retrieval.records
        if r.record_id in sent
    ]
    return build_evidence_messages(
        case_id=retrieval.case_id,
        question=retrieval.question,
        records=records,
        excluded_record_ids={*retrieval.excluded_record_ids, *excluded_record_ids},
        prior_analysis=prior_analysis,
        media=media_inputs(media) if media else None,
    )


def sent_record_ids(
    retrieval: RetrievalResult, media: Mapping[str, MediaPart] | None = None
) -> set[str]:
    """Records of ``retrieval`` that reach the prompt: every text record, and image/PDF
    records only with a media part in ``media``."""
    media = media or {}
    return {
        r.record_id for r in retrieval.records if r.modality == "text" or r.record_id in media
    }


def run_analysis(
    llm: AnalysisLLM,
    retrieval: RetrievalResult,
    *,
    session: Session | None = None,
    excluded_record_ids: Iterable[str] = (),
    prior_analysis: str | None = None,
    media: Mapping[str, MediaPart] | None = None,
) -> AnalysisOutput:
    """Analyze ``retrieval.question`` over the retrieved evidence with exactly one LLM call.

    With a ``session``, each record's stored ``metadata`` is added to the prompt.
    ``prior_analysis`` is passed as context labeled "prior analysis — not evidence".
    Raises ``AnalysisLLMError`` if the provider fails and ``AnalysisOutputError`` if the
    output is not a valid ``AnalysisOutput``; cited IDs are not checked here (see
    ``app.validation``).
    """
    metadata_by_id = (
        record_metadata(session, retrieval.case_id, (r.record_id for r in retrieval.records))
        if session is not None
        else None
    )
    messages = build_analysis_messages(
        retrieval,
        excluded_record_ids=excluded_record_ids,
        prior_analysis=prior_analysis,
        metadata_by_id=metadata_by_id,
        media=media,
    )
    return llm.generate(messages)
