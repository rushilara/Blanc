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


def load(rel: str):
    return json.loads((ROOT / rel).read_text())


def main() -> int:
    evidence_schema = load("contracts/evidence_record.schema.json")
    revision_schema = load("contracts/analysis_revision.schema.json")

    # Fails loudly if a schema itself is malformed.
    Draft202012Validator.check_schema(evidence_schema)
    Draft202012Validator.check_schema(revision_schema)

    errors: list[str] = []
    for i, record in enumerate(load("fixtures/synthetic_records.json")):
        for err in Draft202012Validator(evidence_schema).iter_errors(record):
            errors.append(f"synthetic_records.json[{i}]: {err.message}")
    revision = load("fixtures/synthetic_analysis_revision.json")
    for err in Draft202012Validator(revision_schema).iter_errors(revision):
        errors.append(f"synthetic_analysis_revision.json: {err.message}")

    if errors:
        print("FAIL: fixtures do not match contracts:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS: contracts are valid JSON Schemas and fixtures conform to them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
