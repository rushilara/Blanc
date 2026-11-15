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
structural reason to think so: an edge joining the same two entities with the same
type, a claim with the same wording, or an event with the same wording and the
same timestamp. Wording is compared after ignoring case, spacing and a closing
full stop, and nothing looser: other punctuation counts, because a sign, decimal
point or currency symbol can reverse a finding (`-$500` and `$500`), and similar
wording is not enough, because "Alice transferred the money" and "Alice never
transferred the money" differ by one word, as do changed amounts, dates and names.
An edge's endpoints are the same entity only when both the node ID and the node
itself (label and type) match, so a node ID reused for a different person does
not carry the edge over. Citing the same record is not enough either, because one
record often supports several unrelated facts; and an ID is not enough, because
IDs are regenerated on every LLM run. When identity cannot be established, the
base finding is reported as `unresolved` or `not_supported` and the child finding
as `newly_supported`, so both are shown, rather than a vanished or reversed
conclusion being shown as surviving.

Support is counted in unique record IDs, so a record cited twice counts once.

Possibly the same finding (BLA-63). Strict identity means a finding the rerun merely
rewords shows up twice: the base one `unresolved`, the child one `newly_supported`. To
help the investigator see that, an advisory link is added in `possible_matches`, on both
items. A base finding with no identity match is linked to a `newly_supported` child
finding of the same kind when the child cites at least one record and every record it
cites was cited by the base finding and is still permitted (an edge must also join the
same two entities, of any type). The link never changes `support_status` and only appends
cautious wording to the explanation: "Possibly the same finding as claim-a in the active
analysis: 2 of 3 cited sources remain; please confirm." Model-reused IDs are still not
used, for the reason above: an ID shared by two different findings would make a changed
conclusion look like the same one with less support. Confirming a link is the
investigator's call (the UI is BLA-36).

Wording rule (contracts/README.md): an exclusion changes what the active analysis
can conclude, not what happened. Explanations say "no longer supported by the
remaining evidence", never "did not happen" or "disproved".
"""

import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.contracts import (
    AnalysisRevision,
    ComparisonItem,
    ComparisonItemKind,
    PossibleMatch,
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
    base_entities, compared_entities = _entities(base), _entities(compared)

    items: list[ComparisonItem] = []
    for kind in _KINDS:
        base_items = _items(base, kind)
        compared_items = _items(compared, kind)
        pairs = _match(kind, base_items, compared_items, base_entities, compared_entities)
        matched_compared = {id(after) for after in pairs.values()}
        vanished: list[tuple[Any, ComparisonItem]] = []
        new: list[tuple[Any, ComparisonItem]] = []
        for before in base_items:
            after = pairs.get(id(before))
            item = _classify(kind, before, after, excluded_now)
            items.append(item)
            if after is None:
                vanished.append((before, item))
        for after in compared_items:
            if id(after) not in matched_compared:
                item = _new_item(kind, after)
                items.append(item)
                new.append((after, item))
        linked = _possible_matches(
            kind, vanished, new, set(newly_excluded), base_entities, compared_entities
        )
        items = [linked.get(id(item), item) for item in items]

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


def _entities(revision: AnalysisRevision) -> dict[str, tuple[str, str]]:
    """Each graph node ID mapped to what it names: its label wording and type."""
    return {node.id: (_wording(node.label), node.type) for node in revision.graph.nodes}


def _match(
    kind: ComparisonItemKind,
    base_items: Sequence[Any],
    compared_items: Sequence[Any],
    base_entities: dict[str, tuple[str, str]],
    compared_entities: dict[str, tuple[str, str]],
) -> dict[int, Any]:
    """Pair each base finding with its counterpart in the child, if one can be identified.

    Each child finding is used at most once, in base order:

    - an edge: the same type between the same two node IDs, each naming the same entity
      (label and type) in both revisions;
    - a claim: the same wording (ignoring case, spacing and a closing full stop);
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
            key = _edge_identity(before, base_entities)
            if key is None:
                continue
            for after in free(compared_items):
                if _edge_identity(after, compared_entities) == key:
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


def _edge_identity(edge: Any, entities: dict[str, tuple[str, str]]) -> tuple[Any, ...] | None:
    """What must be equal for an edge to be the same finding; None if an endpoint is unknown."""
    endpoints = _edge_endpoints(edge, entities)
    return None if endpoints is None else (*endpoints, edge.type)


def _edge_endpoints(edge: Any, entities: dict[str, tuple[str, str]]) -> tuple[Any, ...] | None:
    """The two entities an edge joins, in order (node ID and what it names, for each end);
    None if an endpoint is unknown."""
    source, target = entities.get(edge.source), entities.get(edge.target)
    if source is None or target is None:
        return None
    return (edge.source, source, edge.target, target)


def _possible_matches(
    kind: ComparisonItemKind,
    vanished: Sequence[tuple[Any, ComparisonItem]],
    new: Sequence[tuple[Any, ComparisonItem]],
    newly_excluded: set[str],
    base_entities: dict[str, tuple[str, str]],
    compared_entities: dict[str, tuple[str, str]],
) -> dict[int, ComparisonItem]:
    """Advisory links between unmatched base findings and newly supported child findings.

    A base finding with no identity match (`unresolved` or `not_supported`) is linked to a
    `newly_supported` child finding of the same kind when the child cites at least one
    record and every record it cites was cited by the base finding and is still permitted.
    Edges must also join the same two entities (as in `_edge_identity`, but any type).
    Nothing else is compared: the link only says "possibly the same finding" and leaves
    both statuses as they are. The link is listed on both items; each list is ordered by
    the number of shared records (most first), then ref ID.

    Returns the linked items, with `possible_matches` set and a cautious sentence appended
    to their explanation, keyed by `id()` of the item they replace.
    """
    base_links: dict[int, list[PossibleMatch]] = {}
    new_links: dict[int, list[PossibleMatch]] = {}
    for before, base_item in vanished:
        cited = base_item.base_evidence_ids
        remaining = [rid for rid in cited if rid not in newly_excluded]
        for after, new_item in new:
            child_cited = set(new_item.compared_evidence_ids)
            if not child_cited or not child_cited <= set(remaining):
                continue
            if kind == "edge":
                ends = _edge_endpoints(before, base_entities)
                if ends is None or ends != _edge_endpoints(after, compared_entities):
                    continue
            shared = [rid for rid in cited if rid in child_cited]
            counts = {"remaining_sources": len(remaining), "original_sources": len(cited)}
            base_links.setdefault(id(base_item), []).append(
                PossibleMatch(ref_id=after.id, shared_evidence_ids=shared, **counts)
            )
            new_links.setdefault(id(new_item), []).append(
                PossibleMatch(ref_id=before.id, shared_evidence_ids=shared, **counts)
            )

    linked: dict[int, ComparisonItem] = {}
    for side, links, items in (
        ("base", base_links, [item for _, item in vanished]),
        ("compared", new_links, [item for _, item in new]),
    ):
        for item in items:
            matches = links.get(id(item))
            if not matches:
                continue
            matches.sort(key=lambda m: (-len(m.shared_evidence_ids), m.ref_id))
            explanation = f"{item.explanation} {_link_sentence(side, matches)}"
            linked[id(item)] = item.model_copy(
                update={"possible_matches": matches, "explanation": explanation}
            )
    return linked


def _link_sentence(side: str, matches: Sequence[PossibleMatch]) -> str:
    """Cautious wording for a link: possibly the same finding, N of M remain, please confirm."""
    if side == "base":
        first = matches[0]
        refs = " or ".join(m.ref_id for m in matches)
        return (
            f"Possibly the same finding as {refs} in the active analysis: "
            f"{_remain(first)}; please confirm."
        )
    refs = " or ".join(f"{m.ref_id} ({_remain(m)})" for m in matches)
    return f"Possibly the same finding as {refs} in the base revision; please confirm."


def _remain(match: PossibleMatch) -> str:
    n, m = match.remaining_sources, match.original_sources
    return f"{n} of {m} cited {'source remains' if m == 1 else 'sources remain'}"


def _wording(text: str) -> str:
    """Case, spacing and a closing full stop are ignored; every other character counts."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).rstrip(" .")


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
