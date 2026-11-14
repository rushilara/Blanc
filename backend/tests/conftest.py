"""Shared test fixtures. Paths resolve from the repo root so tests run from backend/."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

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


RAW = {key: load_json(FIXTURES_DIR / name) for key, name in FIXTURE_FILES.items()}


@pytest.fixture
def raw() -> dict[str, object]:
    """Fresh, mutable copies of the raw fixture JSON, keyed by short name."""
    return deepcopy(RAW)
