"""Minimal cross-reference validator for the synthetic Blanc fixture.

Not a forensic parser, retrieval engine or LLM. Demonstrates invariant checks that
must be enforced server-side in the actual application.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_revision(records: list[dict], analysis: dict) -> list[str]:
    errors: list[str] = []
    ids = [r['record_id'] for r in records]
    if len(ids) != len(set(ids)):
        errors.append('duplicate source record IDs')
    known = set(ids)
    case_id = analysis['case_id']
    for r in records:
        if r['case_id'] != case_id:
            errors.append(f"cross-case record: {r['record_id']}")
    excluded = set(analysis.get('excluded_record_ids', []))
    retrieved = set(analysis.get('retrieved_record_ids', []))
    if not retrieved.issubset(known):
        errors.append('retrieved IDs must exist in the case corpus')
    if retrieved & excluded:
        errors.append('excluded source record appeared in retrieval set')
    valid_support = retrieved - excluded
    nodes = {n['id'] for n in analysis['graph']['nodes']}
    items = [('claim', x) for x in analysis.get('claims', [])]
    items += [('edge', x) for x in analysis['graph']['edges']]
    items += [('event', x) for x in analysis['timeline_events']]
    for kind, item in items:
        refs = set(item.get('evidence_ids', []))
        if not refs:
            errors.append(f"{kind} {item['id']} has no evidence IDs")
        if not refs.issubset(valid_support):
            errors.append(f"{kind} {item['id']} cites missing, excluded or un-retrieved evidence")
    for edge in analysis['graph']['edges']:
        if edge['source'] not in nodes or edge['target'] not in nodes:
            errors.append(f"edge {edge['id']} references unknown graph node")
    return errors


def parse_datetime(value: object) -> datetime | None:
    """Parse an ISO-8601 date-time with a UTC offset ('Z' or +HH:MM). Returns None if
    the value is unparseable or naive (no offset), so instants compare correctly."""
    if not isinstance(value, str):
        return None
    text = value[:-1] + '+00:00' if value.endswith(('Z', 'z')) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _parse_fields(errors: list[str], fields: list[tuple[str, object]]) -> dict[str, datetime]:
    parsed: dict[str, datetime] = {}
    for label, value in fields:
        dt = parse_datetime(value)
        if dt is None:
            errors.append(f"{label} is not an ISO-8601 date-time with an offset: {value!r}")
        else:
            parsed[label] = dt
    return parsed


def _revision_items(analysis: dict) -> dict[tuple[str, str], dict]:
    """Index a revision's citable items by (kind, id)."""
    index: dict[tuple[str, str], dict] = {}
    for c in analysis.get('claims', []):
        index[('claim', c['id'])] = c
    for n in analysis['graph']['nodes']:
        index[('node', n['id'])] = n
    for e in analysis['graph']['edges']:
        index[('edge', e['id'])] = e
    for ev in analysis['timeline_events']:
        index[('event', ev['id'])] = ev
    return index


def validate_board(records: list[dict], revisions: list[dict], board: dict) -> list[str]:
    """Cross-reference checks for an InvestigationBoard snapshot."""
    errors: list[str] = []
    case_id = board['case_id']
    case_records = {r['record_id'] for r in records if r['case_id'] == case_id}
    by_id = {a['analysis_id']: a for a in revisions if a['case_id'] == case_id}

    parent = board.get('parent_revision')
    if parent is not None and parent >= board['revision']:
        errors.append('parent_revision must be lower than revision (snapshots are append-only)')
    stamps = _parse_fields(errors, [('created_at', board['created_at']), ('updated_at', board['updated_at'])])
    if len(stamps) == 2 and stamps['updated_at'] < stamps['created_at']:
        errors.append('updated_at is before created_at')
    updated = stamps.get('updated_at')
    later = [(f"board item {i['item_id']} added_at", i['added_at']) for i in board['items']]
    later += [(f"note {n['note_id']} created_at", n['created_at']) for n in board['notes']]
    for label, dt in _parse_fields(errors, later).items():
        if updated is not None and dt > updated:
            errors.append(f"{label} is after the snapshot's updated_at")
    active = board.get('active_analysis_id')
    if active is not None and active not in by_id:
        errors.append(f"active_analysis_id {active} is not a revision of case {case_id}")

    item_ids = [i['item_id'] for i in board['items']]
    if len(item_ids) != len(set(item_ids)):
        errors.append('duplicate board item IDs')
    seen_refs = set()
    for item in board['items']:
        label = f"board item {item['item_id']}"
        key = (item['source_analysis_id'], item['kind'], item['ref_id'])
        if key in seen_refs:
            errors.append(f"{label} pins the same finding twice")
        seen_refs.add(key)
        cited = set(item['evidence_ids'])
        if not cited.issubset(case_records):
            errors.append(f"{label} cites evidence not in case {case_id}")
        source = by_id.get(item['source_analysis_id'])
        if source is None:
            errors.append(f"{label} source_analysis_id is not a revision of case {case_id}")
            continue
        if cited & set(source.get('excluded_record_ids', [])):
            errors.append(f"{label} cites evidence excluded in its source revision")
        ref = _revision_items(source).get((item['kind'], item['ref_id']))
        if ref is None:
            errors.append(f"{label} ref_id {item['ref_id']} is not a {item['kind']} in {source['analysis_id']}")
            continue
        if not cited.issubset(set(ref.get('evidence_ids', []))):
            errors.append(f"{label} cites evidence its source {item['kind']} does not cite")
        if 'status' in ref and item.get('status') != ref['status']:
            errors.append(f"{label} status differs from its source {item['kind']} (pinning must not change status)")
        if item['kind'] != 'node' and 'status' not in item:
            errors.append(f"{label} is a {item['kind']} and needs a status")

    note_ids = [n['note_id'] for n in board['notes']]
    if len(note_ids) != len(set(note_ids)):
        errors.append('duplicate board note IDs')
    for note in board['notes']:
        target = note.get('target_item_id')
        if target is not None and target not in item_ids:
            errors.append(f"note {note['note_id']} targets unknown board item {target}")
    return errors


def validate_comparison(revisions: list[dict], comparison: dict) -> list[str]:
    """Cross-reference checks for a RevisionComparison between a parent and child revision."""
    errors: list[str] = []
    by_id = {a['analysis_id']: a for a in revisions}
    base = by_id.get(comparison['base_analysis_id'])
    compared = by_id.get(comparison['compared_analysis_id'])
    if base is None or compared is None:
        return ['base_analysis_id and compared_analysis_id must both be known revisions']
    _parse_fields(errors, [('comparison created_at', comparison['created_at'])])
    case_id = comparison['case_id']
    if base['case_id'] != case_id or compared['case_id'] != case_id:
        errors.append('comparison and both revisions must share one case_id')
    if compared.get('parent_analysis_id') != base['analysis_id']:
        errors.append('compared revision must be a child of the base revision')
    delta = set(compared['excluded_record_ids']) - set(base['excluded_record_ids'])
    if set(comparison['excluded_record_ids']) != delta:
        errors.append('excluded_record_ids must equal the exclusions added between base and compared revisions')

    base_items = _revision_items(base)
    compared_items = _revision_items(compared)
    compared_excluded = set(compared['excluded_record_ids'])
    for n, item in enumerate(comparison['items']):
        label = f"comparison item {n}"
        b, c, status = item['base_ref_id'], item['compared_ref_id'], item['support_status']
        if b is None and c is None:
            errors.append(f"{label} needs base_ref_id or compared_ref_id")
        if b is not None:
            ref = base_items.get((item['kind'], b))
            if ref is None:
                errors.append(f"{label} base_ref_id {b} is not a {item['kind']} in the base revision")
            elif set(item['base_evidence_ids']) != set(ref['evidence_ids']):
                errors.append(f"{label} base_evidence_ids do not match the base revision")
        if c is not None:
            ref = compared_items.get((item['kind'], c))
            if ref is None:
                errors.append(f"{label} compared_ref_id {c} is not a {item['kind']} in the compared revision")
            elif set(item['compared_evidence_ids']) != set(ref['evidence_ids']):
                errors.append(f"{label} compared_evidence_ids do not match the compared revision")
        if b is None and item['base_evidence_ids']:
            errors.append(f"{label} has base_evidence_ids but no base_ref_id")
        if c is None and item['compared_evidence_ids']:
            errors.append(f"{label} has compared_evidence_ids but no compared_ref_id")
        if set(item['compared_evidence_ids']) & compared_excluded:
            errors.append(f"{label} compared side cites an excluded record")
        if status in ('still_supported', 'weakened') and (b is None or c is None):
            errors.append(f"{label} {status} needs both base_ref_id and compared_ref_id")
        if status == 'newly_supported' and (b is not None or c is None):
            errors.append(f"{label} newly_supported needs only compared_ref_id")
        if status == 'not_supported' and (b is None or item['compared_evidence_ids']):
            errors.append(f"{label} not_supported needs base_ref_id and no compared evidence")
    errors += _possible_match_errors(comparison)
    return errors


def _possible_match_errors(comparison: dict) -> list[str]:
    """Optional possible_matches (BLA-63): an advisory link, listed on both items, between an
    unmatched unresolved/not_supported base item and a newly_supported item of the same kind."""
    errors: list[str] = []
    excluded = set(comparison['excluded_record_ids'])
    items = comparison['items']
    vanished = {(i['kind'], i['base_ref_id']): i for i in items
                if i['compared_ref_id'] is None and i['support_status'] in ('unresolved', 'not_supported')}
    new = {(i['kind'], i['compared_ref_id']): i for i in items
           if i['base_ref_id'] is None and i['support_status'] == 'newly_supported'}
    for n, item in enumerate(items):
        if 'possible_matches' not in item:
            continue
        label = f"comparison item {n}"
        on_base = item['compared_ref_id'] is None
        own = (item['kind'], item['base_ref_id'] if on_base else item['compared_ref_id'])
        if own not in (vanished if on_base else new):
            errors.append(f"{label} possible_matches is only for an unmatched base item or a newly_supported item")
            continue
        others = new if on_base else vanished
        refs = [m['ref_id'] for m in item['possible_matches']]
        if len(refs) != len(set(refs)):
            errors.append(f"{label} possible_matches repeats a ref_id")
        for match in item['possible_matches']:
            other = others.get((item['kind'], match['ref_id']))
            if other is None:
                errors.append(f"{label} possible match {match['ref_id']} is not on the other side")
                continue
            base_item, new_item = (item, other) if on_base else (other, item)
            shared = set(match['shared_evidence_ids'])
            if not shared <= set(base_item['base_evidence_ids']) or not shared <= set(new_item['compared_evidence_ids']):
                errors.append(f"{label} possible match {match['ref_id']} shares records not cited by both")
            if shared & excluded:
                errors.append(f"{label} possible match {match['ref_id']} shares excluded records")
            cited = set(base_item['base_evidence_ids'])
            if match['original_sources'] != len(cited) or match['remaining_sources'] != len(cited - excluded):
                errors.append(f"{label} possible match {match['ref_id']} source counts do not match the base item")
            back = [dict(m, ref_id=match['ref_id']) for m in other.get('possible_matches', []) if m['ref_id'] == own[1]]
            if back != [match]:
                errors.append(f"{label} possible match {match['ref_id']} is not listed the same way on the other item")
    return errors


def main() -> None:
    fx = ROOT/'fixtures'
    records = json.loads((fx/'synthetic_records.json').read_text())
    analysis = json.loads((fx/'synthetic_analysis_revision.json').read_text())
    child = json.loads((fx/'synthetic_analysis_revision_excluded.json').read_text())
    board = json.loads((fx/'synthetic_board.json').read_text())
    comparison = json.loads((fx/'synthetic_revision_comparison.json').read_text())
    errors = validate_revision(records, analysis)
    errors += [f'child revision: {e}' for e in validate_revision(records, child)]
    errors += [f'board: {e}' for e in validate_board(records, [analysis, child], board)]
    errors += [f'comparison: {e}' for e in validate_comparison([analysis, child], comparison)]
    if errors:
        raise SystemExit('Validation errors:\n- ' + '\n- '.join(errors))
    print('PASS: synthetic case has valid source IDs, citations, graph references, exclusions, board and comparison cross-references')

if __name__ == '__main__':
    main()
