"""Citation and provenance sanitizing for LLM-built revisions (BLA-17).

LLM output is not evidence. Before a revision is validated and saved, every citation it
makes is checked against the permitted support set: records of the same case that were
retrieved for this revision and not excluded (``app.validation.permitted_evidence_ids``).

Policy: strip, don't reject.

- Invalid evidence IDs (fabricated, from another case, excluded, or not retrieved) are
  removed from every claim, edge, event and node. Each removal is a warning naming the IDs.
- A claim, edge or event that keeps at least one valid ID is kept, status unchanged.
  One left with none is dropped (the schema requires ``evidence_ids`` on them), and a short
  note is added to ``unresolved_questions`` so the investigator sees what was lost.
- An edge whose ``source`` or ``target`` is not a graph node is dropped.
- A node that cited only invalid records is dropped when no surviving edge references it;
  a node still referenced by an edge is kept, with ``evidence_ids`` omitted. Nodes that
  never cited anything are left alone.
- The free-text ``answer`` is never rewritten, but it may rest on findings that were just
  dropped. Whenever a claim, edge or event is dropped, ``dropped_findings`` counts it
  (``answer_needs_review`` is then true, for the caller to mark the revision for review)
  and one note in ``unresolved_questions`` says the answer may overstate the evidence.
- Problems stripping cannot fix (duplicate or cross-case corpus records, retrieved IDs
  outside the corpus or also excluded, duplicate item IDs, invented exact timestamps) are
  returned as errors, using the same messages as ``validate_revision``.

Notes never quote an item that cited an excluded record, and never contain record IDs or
record content, so an exclusion cannot leak back into the revision through them.

Intended use: build the revision from LLM output and server metadata, call
``sanitize_citations``, then ``validate_revision`` on the result, then save. When
``errors`` is empty the sanitized revision passes ``validate_revision``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.contracts import AnalysisRevision, Claim, EvidenceRecord, GraphEdge, TimelineEvent
from app.validation import case_record_ids, permitted_evidence_ids, revision_integrity_errors

__all__ = ["ANSWER_REVIEW_NOTE", "SanitizeResult", "sanitize_citations"]

_QUOTE_LIMIT = 80
# How each kind is named in investigator-facing notes.
_NOTE_NOUN = {"claim": "claim", "edge": "relationship", "event": "timeline event"}
ANSWER_REVIEW_NOTE = (
    "Some findings behind this answer were removed because no permitted evidence supports "
    "them; the answer may overstate what the evidence shows."
)


@dataclass(frozen=True)
class SanitizeResult:
    """Outcome of ``sanitize_citations``."""

    # A new revision; the input is never modified.
    analysis: AnalysisRevision
    # What was stripped or dropped, naming item and evidence IDs. Empty when nothing changed.
    warnings: list[str] = field(default_factory=list)
    # Problems stripping cannot repair. Non-empty means the revision needs review.
    errors: list[str] = field(default_factory=list)
    # Claims, edges and events dropped for lack of permitted support or a missing node.
    dropped_findings: int = 0

    @property
    def answer_needs_review(self) -> bool:
        """The unchanged ``answer`` may assert something whose support was removed."""
        return self.dropped_findings > 0


def _fmt(ids: Sequence[str]) -> str:
    return ", ".join(sorted(set(ids)))


def _quote(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > _QUOTE_LIMIT:
        text = text[: _QUOTE_LIMIT - 1].rstrip() + "…"
    return f'"{text}"'


class _Sanitizer:
    def __init__(self, records: Sequence[EvidenceRecord], analysis: AnalysisRevision) -> None:
        self.analysis = analysis
        self.permitted = permitted_evidence_ids(records, analysis)
        self.known = case_record_ids(records, analysis.case_id)
        self.all_records = {r.record_id for r in records}
        self.excluded = set(analysis.excluded_record_ids)
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.dropped = 0

    def strip(self, label: str, cited: Sequence[str]) -> tuple[list[str], bool]:
        """Return the permitted IDs of ``cited`` and whether any cited ID was excluded."""
        kept = [i for i in cited if i in self.permitted]
        invalid = [i for i in cited if i not in self.permitted]
        reasons = (
            ("excluded", [i for i in invalid if i in self.excluded]),
            (
                "from another case",
                [i for i in invalid if i in self.all_records - self.known - self.excluded],
            ),
            ("unknown", [i for i in invalid if i not in self.all_records | self.excluded]),
            ("not retrieved", [i for i in invalid if i in self.known - self.excluded]),
        )
        for reason, ids in reasons:
            if ids:
                self.warnings.append(f"{label}: removed {reason} evidence IDs: {_fmt(ids)}")
        return kept, bool(set(cited) & self.excluded)

    def note(self, kind: str, quoted: str | None) -> None:
        what = _NOTE_NOUN[kind]
        if quoted is None:
            text = f"A {what} was removed because no permitted evidence supports it."
        else:
            text = f"{what.capitalize()} {quoted} was removed: no permitted evidence supports it."
        self.add_note(text)

    def add_note(self, text: str) -> None:
        if text not in self.notes and text not in self.analysis.unresolved_questions:
            self.notes.append(text)

    def supported[T: (Claim, GraphEdge, TimelineEvent)](
        self, kind: str, items: Sequence[T], describe: dict[str, str]
    ) -> list[T]:
        """Strip invalid citations from claims, edges or events; drop unsupported ones."""
        out = []
        for item in items:
            label = f"{kind} {item.id}"
            kept, cited_excluded = self.strip(label, item.evidence_ids)
            if kept:
                out.append(item.model_copy(update={"evidence_ids": kept}))
                continue
            self.warnings.append(f"{label} dropped: no permitted evidence left")
            self.dropped += 1
            self.note(kind, None if cited_excluded else describe.get(item.id))
        return out

    def run(self) -> AnalysisRevision:
        analysis = self.analysis
        nodes_by_id = {n.id: n for n in analysis.graph.nodes}

        claims = self.supported(
            "claim", analysis.claims, {c.id: _quote(c.text) for c in analysis.claims}
        )
        events = self.supported(
            "event",
            analysis.timeline_events,
            {e.id: _quote(e.description) for e in analysis.timeline_events},
        )
        edges = self.supported("edge", analysis.graph.edges, self._edge_descriptions())

        connected = []
        for edge in edges:
            missing = [end for end in (edge.source, edge.target) if end not in nodes_by_id]
            if missing:
                self.warnings.append(
                    f"edge {edge.id} dropped: references unknown graph nodes {_fmt(missing)}"
                )
                self.dropped += 1
            else:
                connected.append(edge)
        referenced = {end for edge in connected for end in (edge.source, edge.target)}

        nodes = []
        for node in analysis.graph.nodes:
            if not node.evidence_ids:
                nodes.append(node)
                continue
            kept, _ = self.strip(f"node {node.id}", node.evidence_ids)
            if kept:
                nodes.append(node.model_copy(update={"evidence_ids": kept}))
            elif node.id in referenced:
                nodes.append(node.model_copy(update={"evidence_ids": None}))
            else:
                self.warnings.append(
                    f"node {node.id} dropped: no permitted evidence and no supported edges"
                )

        if self.dropped:
            self.warnings.append(
                f"answer needs review: {self.dropped} supporting finding(s) were dropped"
            )
            if ANSWER_REVIEW_NOTE not in analysis.unresolved_questions:
                self.notes.insert(0, ANSWER_REVIEW_NOTE)  # summary before per-item notes

        graph = analysis.graph.model_copy(update={"nodes": nodes, "edges": connected})
        return analysis.model_copy(
            update={
                "claims": claims,
                "graph": graph,
                "timeline_events": events,
                "unresolved_questions": [*analysis.unresolved_questions, *self.notes],
            }
        ).model_copy(deep=True)

    def _edge_descriptions(self) -> dict[str, str]:
        labels = {n.id: n.label for n in self.analysis.graph.nodes}
        out = {}
        for edge in self.analysis.graph.edges:
            source = labels.get(edge.source, edge.source)
            target = labels.get(edge.target, edge.target)
            out[edge.id] = _quote(f"{source} {edge.type.replace('_', ' ')} {target}")
        return out


def sanitize_citations(
    records: Sequence[EvidenceRecord], analysis: AnalysisRevision
) -> SanitizeResult:
    """Strip citations outside the permitted support set from ``analysis``.

    ``records`` is the case corpus, as for ``validate_revision``. Returns a new revision,
    the warnings describing every change, and any errors stripping cannot repair. A
    revision that already cites only permitted records comes back equal, with no warnings.
    """
    sanitizer = _Sanitizer(records, analysis)
    cleaned = sanitizer.run()
    errors = revision_integrity_errors(records, analysis)
    return SanitizeResult(
        analysis=cleaned,
        warnings=sanitizer.warnings,
        errors=errors,
        dropped_findings=sanitizer.dropped,
    )
