"""Support diff between a revision and the child produced by excluding evidence (BLA-34).

Deterministic and based only on the source record IDs each finding cites: no LLM
call, so the same two revisions always give the same comparison.

For every claim, edge and event, `compare_revisions` says how its support changed:

- `still_supported`: present in both, and the child still cites every record the base
  cited.
- `weakened`: present in both, but the child no longer cites at least one record the base
  cited (an excluded record, or one it dropped or replaced). The explanation names them.
- `not_supported`: present in the base only, and every record it cited is now excluded.
- `unresolved`: the child marks it unresolved, or no finding in the child can be
  identified as this one even though some of its records are still permitted. A
  finding with an independent second source is therefore never reported as
  `not_supported` when only one of its sources is excluded.
- `newly_supported`: present in the child only.

Identity is conservative. Two findings are the same finding only when there is a
structural reason to think so: an edge joining the same two nodes with the same
type, a claim with the same wording, or an event with the same wording and the
same timestamp. Wording is compared after ignoring case, spacing and punctuation,
and nothing looser: similar wording is not enough, because "Alice transferred the
money" and "Alice never transferred the money" differ by one word, as do changed
amounts, dates and names. Citing the same record is not enough either, because one
record often supports several unrelated facts; and an ID is not enough, because
IDs are regenerated on every LLM run. When identity cannot be established, the
base finding is reported as `unresolved` or `not_supported` and the child finding
as `newly_supported`, so both are shown, rather than a vanished or reversed
conclusion being shown as surviving.

Support is counted in unique record IDs, so a record cited twice counts once.

Wording rule (contracts/README.md): an exclusion changes what the active analysis
can conclude, not what happened. Explanations say "no longer supported by the
remaining evidence", never "did not happen" or "disproved".
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.contracts import (
    AnalysisRevision,
    ComparisonItem,
    ComparisonItemKind,
    RevisionComparison,
    SupportStatus,
)

_KINDS: tuple[ComparisonItemKind, ...] = ("claim", "edge", "event")


def compare_revisions(
    base: AnalysisRevision,
    compared: AnalysisRevision,
    *,
    created_at: datetime | None = None,
) -> RevisionComparison:
    """Compare `base` with `compared`, its child after an exclusion.

    Everything in the result depends only on the two revisions except `created_at`,
    which defaults to the current time; pass the timestamp you store to make the whole
    result reproducible.

    Raises `ValueError` if the two are not a parent and child in the same case.
    """
    if base.case_id != compared.case_id:
        raise ValueError(
            f"revisions belong to different cases: {base.case_id!r} and {compared.case_id!r}"
        )
    if compared.parent_analysis_id != base.analysis_id:
        raise ValueError(
            f"{compared.analysis_id} is not a child of {base.analysis_id} "
            f"(its parent is {compared.parent_analysis_id!r})"
        )

    excluded_before = set(base.excluded_record_ids)
    newly_excluded = [rid for rid in compared.excluded_record_ids if rid not in excluded_before]
    excluded_now = set(compared.excluded_record_ids)

    items: list[ComparisonItem] = []
    for kind in _KINDS:
        base_items = _items(base, kind)
        compared_items = _items(compared, kind)
        pairs = _match(kind, base_items, compared_items)
        matched_compared = {id(after) for after in pairs.values()}
        for before in base_items:
            after = pairs.get(id(before))
            items.append(_classify(kind, before, after, excluded_now))
        for after in compared_items:
            if id(after) not in matched_compared:
                items.append(_new_item(kind, after))

    return RevisionComparison(
        schema_version="1.0",
        case_id=base.case_id,
        base_analysis_id=base.analysis_id,
        compared_analysis_id=compared.analysis_id,
        excluded_record_ids=newly_excluded,
        items=items,
        created_at=created_at or datetime.now(UTC),
    )


def _items(revision: AnalysisRevision, kind: ComparisonItemKind) -> list[Any]:
    if kind == "claim":
        return list(revision.claims)
    if kind == "edge":
        return list(revision.graph.edges)
    return list(revision.timeline_events)


def _match(
    kind: ComparisonItemKind, base_items: Sequence[Any], compared_items: Sequence[Any]
) -> dict[int, Any]:
    """Pair each base finding with its counterpart in the child, if one can be identified.

    Each child finding is used at most once, in base order:

    - an edge: the same two nodes and the same type;
    - a claim: the same wording (ignoring case, spacing and punctuation);
    - an event: the same wording and the same timestamp.

    There is no rule based on similar wording, shared records or IDs; see the module
    docstring.
    """
    pairs: dict[int, Any] = {}
    taken: set[int] = set()

    def pair(before: Any, after: Any) -> None:
        pairs[id(before)] = after
        taken.add(id(after))

    def free(items: Sequence[Any]) -> list[Any]:
        return [item for item in items if id(item) not in taken]

    if kind == "edge":
        for before in base_items:
            key = (before.source, before.target, before.type)
            for after in free(compared_items):
                if (after.source, after.target, after.type) == key:
                    pair(before, after)
                    break
        return pairs

    for before in base_items:
        key = _identity(kind, before)
        for after in free(compared_items):
            if _identity(kind, after) == key:
                pair(before, after)
                break
    return pairs


def _identity(kind: ComparisonItemKind, item: Any) -> tuple[str, str | None]:
    """What must be equal for a claim or event to be the same finding."""
    if kind == "claim":
        return _wording(item.text), None
    timestamp = item.timestamp.strip() if item.timestamp is not None else None
    return _wording(item.description), timestamp


def _wording(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def _unique(ids: Sequence[str]) -> list[str]:
    """The IDs in order, each once: a record cited twice is still one supporting record."""
    return list(dict.fromkeys(ids))


def _classify(
    kind: ComparisonItemKind, before: Any, after: Any | None, excluded_now: set[str]
) -> ComparisonItem:
    cited_before = _unique(before.evidence_ids)
    lost = [rid for rid in cited_before if rid in excluded_now]

    if after is None:
        remaining = [rid for rid in cited_before if rid not in excluded_now]
        if remaining:
            status: SupportStatus = "unresolved"
            explanation = (
                f"{_ids(remaining)} {_is(remaining)} still permitted, but no finding in the "
                "active analysis could be identified as this one, so it is not established "
                "by the remaining evidence. That does not show it did not happen."
            )
        else:
            status = "not_supported"
            explanation = (
                f"Its only supporting {_noun(lost)}, {_ids(lost)}, {_is(lost)} excluded, so the "
                "active analysis can no longer conclude this. "
                "The exclusion does not show it did not happen."
            )
        return ComparisonItem(
            kind=kind,
            base_ref_id=before.id,
            compared_ref_id=None,
            support_status=status,
            base_evidence_ids=cited_before,
            compared_evidence_ids=[],
            explanation=explanation,
        )

    cited_after = _unique(after.evidence_ids)
    if getattr(after, "status", None) == "unresolved":
        status = "unresolved"
        explanation = (
            "The active analysis marks this unresolved: the remaining evidence is not "
            "enough to decide. That does not show it did not happen."
        )
    elif dropped := [rid for rid in cited_before if rid not in cited_after]:
        status = "weakened"
        explanation = f"Still supported by {_ids(cited_after)}, but it no longer rests on "
        explanation += "; ".join(_dropped_reasons(dropped, excluded_now)) + "."
    else:
        status = "still_supported"
        explanation = (
            f"Still supported by {_ids(cited_after)}, which {_was(cited_after)} not excluded."
        )
    return ComparisonItem(
        kind=kind,
        base_ref_id=before.id,
        compared_ref_id=after.id,
        support_status=status,
        base_evidence_ids=cited_before,
        compared_evidence_ids=cited_after,
        explanation=explanation,
    )


def _new_item(kind: ComparisonItemKind, after: Any) -> ComparisonItem:
    cited = _unique(after.evidence_ids)
    return ComparisonItem(
        kind=kind,
        base_ref_id=None,
        compared_ref_id=after.id,
        support_status="newly_supported",
        base_evidence_ids=[],
        compared_evidence_ids=cited,
        explanation=f"Appears only in this revision, supported by {_ids(cited)}.",
    )


def _dropped_reasons(dropped: Sequence[str], excluded_now: set[str]) -> list[str]:
    """Why each record the base cited is missing from the child, excluded records first."""
    excluded = [rid for rid in dropped if rid in excluded_now]
    uncited = [rid for rid in dropped if rid not in excluded_now]
    reasons = []
    if excluded:
        reasons.append(f"{_ids(excluded)}, which {_is(excluded)} excluded")
    if uncited:
        reasons.append(f"{_ids(uncited)}, which the active analysis no longer cites")
    return reasons


def _ids(ids: Sequence[str]) -> str:
    return ", ".join(ids)


def _noun(ids: Sequence[str]) -> str:
    return "record" if len(ids) == 1 else "records"


def _is(ids: Sequence[str]) -> str:
    return "is" if len(ids) == 1 else "are"


def _was(ids: Sequence[str]) -> str:
    return "was" if len(ids) == 1 else "were"
