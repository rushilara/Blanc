"""Minimal cross-reference validator for the synthetic CaseLens fixture.

Not a forensic parser, retrieval engine or LLM. Demonstrates invariant checks that
must be enforced server-side in the actual application.
"""
from __future__ import annotations
import json
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


def main() -> None:
    records = json.loads((ROOT/'fixtures'/'synthetic_records.json').read_text())
    analysis = json.loads((ROOT/'fixtures'/'synthetic_analysis_revision.json').read_text())
    errors = validate_revision(records, analysis)
    if errors:
        raise SystemExit('Validation errors:\n- ' + '\n- '.join(errors))
    print('PASS: synthetic case has valid source IDs, citations, graph references and exclusions')

if __name__ == '__main__':
    main()
