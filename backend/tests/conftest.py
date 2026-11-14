from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import pytest
from _paths import RAW
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    """A throwaway SQLite file per test; never the developer's real database."""
    return f"sqlite:///{tmp_path / 'blanc-test.db'}"


@pytest.fixture
def app(db_url: str) -> FastAPI:
    # _env_file=None isolates tests from any local .env file.
    return create_app(Settings(_env_file=None, DATABASE_URL=db_url))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Entering the client runs the lifespan, which creates the tables.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def raw() -> dict[str, object]:
    """Fresh, mutable copies of the synthetic fixture JSON, keyed by short name
    (see tests/_paths.py)."""
    return deepcopy(RAW)
