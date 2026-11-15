"""Repo-relative paths and raw fixture data shared by the contract/validation tests.

Paths resolve from the repo root, so tests work when pytest runs from backend/.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
FIXTURES_DIR = REPO_ROOT / "fixtures"

FIXTURE_FILES = {
    "records": "synthetic_records.json",
    "revision": "synthetic_analysis_revision.json",
    "child": "synthetic_analysis_revision_excluded.json",
    "board": "synthetic_board.json",
    "comparison": "synthetic_revision_comparison.json",
}


def load_json(path: Path) -> object:
    return json.loads(path.read_text())


# Loaded once; tests must deepcopy before mutating (the `raw` fixture does this).
RAW = {key: load_json(FIXTURES_DIR / name) for key, name in FIXTURE_FILES.items()}
