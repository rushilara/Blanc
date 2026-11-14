"""Check that the JSON Schemas in contracts/ are valid and that the fixtures conform to them.

Run from the repo root or from starter/:  python starter/validate_schemas.py
Requires: pip install jsonschema
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]

SCHEMAS = {
    "evidence": "contracts/evidence_record.schema.json",
    "revision": "contracts/analysis_revision.schema.json",
    "board": "contracts/investigation_board.schema.json",
    "comparison": "contracts/revision_comparison.schema.json",
}

# (fixture path, schema key, is the fixture a list of instances?)
FIXTURES = [
    ("fixtures/synthetic_records.json", "evidence", True),
    ("fixtures/synthetic_analysis_revision.json", "revision", False),
    ("fixtures/synthetic_analysis_revision_excluded.json", "revision", False),
    ("fixtures/synthetic_board.json", "board", False),
    ("fixtures/synthetic_revision_comparison.json", "comparison", False),
]


def load(rel: str):
    return json.loads((ROOT / rel).read_text())


def main() -> int:
    validators = {}
    for key, rel in SCHEMAS.items():
        schema = load(rel)
        # Fails loudly if a schema itself is malformed.
        Draft202012Validator.check_schema(schema)
        validators[key] = Draft202012Validator(schema)

    errors: list[str] = []
    for rel, key, is_list in FIXTURES:
        data = load(rel)
        instances = list(enumerate(data)) if is_list else [(None, data)]
        name = Path(rel).name
        for i, instance in instances:
            label = f"{name}[{i}]" if i is not None else name
            for err in validators[key].iter_errors(instance):
                path = "/".join(str(p) for p in err.absolute_path)
                errors.append(f"{label} {path}: {err.message}")

    if errors:
        print("FAIL: fixtures do not match contracts:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"PASS: {len(SCHEMAS)} contracts are valid JSON Schemas and {len(FIXTURES)} fixtures conform to them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
