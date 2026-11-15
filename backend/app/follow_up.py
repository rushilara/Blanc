"""Follow-up questions that build on an investigation (BLA-29).

``ask_follow_up`` asks a new question in the context of a saved revision and, optionally,
the investigator's board:

- Retrieval runs fresh over the case corpus for the new question, with the parent's
  exclusions inherited, so an excluded record cannot come back through a follow-up. The
  parent's evidence window (BLA-64) is inherited too unless the request overrides it.
- The result is a new revision whose parent is the revision the follow-up builds on; the
  parent is never changed.
- The parent's question, answer, claims and unresolved questions, and the board's topic
  and pinned findings, go into the prompt's "prior analysis — not evidence" context. The
  model may use them to understand the topic, never to support a conclusion: every claim
  still has to cite the newly retrieved records.

What is never sent:

- Board notes. They are investigator-authored and, per the contract, never passed to the
  model.
- Pinned findings that cite a record excluded in the parent. A board can hold findings
  from earlier revisions with fewer exclusions; sending those would bring excluded evidence
  back in. As with exclusion reruns, this filter is by citation: a pinned finding that
  cites only permitted records can still paraphrase content from a record it was written
  alongside.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.analyses import _analyze_and_save, get_revision, saved_revision_lookup
from app.analysis_llm import AnalysisLLM
from app.analysis_media import AnalysisMediaConfig
from app.boards import _finding, get_board
from app.contracts import AnalysisRevision, EvidenceWindow, InvestigationBoard
from app.embeddings import Embedder
from app.evidence_window import window_of
from app.retrieval import RetrievalConfig

# Caps that keep the context small: it is orientation, not evidence.
MAX_CLAIMS = 10
MAX_UNRESOLVED = 10
MAX_PINNED = 30
MAX_TEXT_CHARS = 500


def ask_follow_up(
    session: Session,
    case_id: str,
    analysis_id: str,
    question: str,
    llm: AnalysisLLM,
    embedder: Embedder,
    *,
    board_id: str | None = None,
    retrieval_config: RetrievalConfig | None = None,
    media_config: AnalysisMediaConfig | None = None,
    evidence_window: EvidenceWindow | None = None,
    now: datetime | None = None,
) -> AnalysisRevision:
    """Ask ``question`` as a follow-up to saved revision ``analysis_id``; save and return
    the new child revision. ``evidence_window`` (BLA-64) defaults to the parent's window.

    Raises ``RevisionNotFoundError`` for an unknown revision, ``BoardNotFoundError`` for a
    board that is not in this case, ``ExcludedRecordLeakError`` if retrieval leaked an
    exclusion (a server bug), and ``AnalysisLLMError`` on provider failure.
    """
    parent = get_revision(session, case_id, analysis_id)
    board = get_board(session, case_id, board_id) if board_id is not None else None
    excluded = list(parent.excluded_record_ids)
    context = follow_up_context(
        parent,
        board,
        pinned_texts(session, case_id, board, excluded) if board is not None else [],
    )
    return _analyze_and_save(
        session,
        case_id,
        question,
        llm,
        embedder,
        excluded=excluded,
        parent_id=parent.analysis_id,
        prior_text=context,
        retrieval_config=retrieval_config,
        media_config=media_config,
        window=evidence_window or window_of(parent),
        now=now,
    )


def follow_up_context(
    parent: AnalysisRevision,
    board: InvestigationBoard | None,
    pinned: Sequence[dict[str, Any]],
) -> str:
    """JSON context for the follow-up prompt: the parent revision and the board's topic and
    pinned findings. Never includes board notes."""
    context: dict[str, Any] = {
        "label": "The investigation so far (orientation only, not evidence)",
        "previous_question": _clip(parent.question),
        "previous_answer": _clip(parent.answer),
        "previous_claims": [
            {"text": _clip(claim.text), "status": claim.status}
            for claim in parent.claims[:MAX_CLAIMS]
        ],
        "unresolved_questions": [
            _clip(question) for question in parent.unresolved_questions[:MAX_UNRESOLVED]
        ],
    }
    if board is not None:
        context["board"] = {"topic": _clip(board.title), "pinned": list(pinned[:MAX_PINNED])}
    return json.dumps(context, ensure_ascii=False)


def pinned_texts(
    session: Session, case_id: str, board: InvestigationBoard, excluded: Sequence[str]
) -> list[dict[str, Any]]:
    """What each pinned finding says, read from its saved source revision, leaving out
    findings that cite any of ``excluded``. Notes are never read."""
    out = set(excluded)
    revisions = {
        revision.analysis_id: revision
        for revision in saved_revision_lookup(
            session, case_id, {item.source_analysis_id for item in board.items}
        )
    }
    pinned: list[dict[str, Any]] = []
    for item in board.items:
        if set(item.evidence_ids) & out:
            continue
        revision = revisions.get(item.source_analysis_id)
        finding = _finding(revision, item.kind, item.ref_id) if revision else None
        if finding is None:
            continue
        text = _clip(_describe(item.kind, finding, revision))
        entry: dict[str, Any] = {"kind": item.kind, "text": text}
        if item.status is not None:
            entry["status"] = item.status
        pinned.append(entry)
    return pinned


def _describe(kind: str, finding: Any, revision: AnalysisRevision | None) -> str:
    if kind == "claim":
        return finding.text
    if kind == "event":
        when = f" ({finding.timestamp})" if finding.timestamp else ""
        return f"{finding.description}{when}"
    if kind == "node":
        return f"{finding.label} ({finding.type})"
    labels = {node.id: node.label for node in revision.graph.nodes} if revision else {}
    text = (
        f"{labels.get(finding.source, finding.source)} "
        f"{finding.type.replace('_', ' ')} {labels.get(finding.target, finding.target)}"
    )
    return f"{text}: {finding.description}" if finding.description else text


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT_CHARS else text[: MAX_TEXT_CHARS - 1].rstrip() + "…"
