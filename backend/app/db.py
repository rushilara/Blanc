"""Database plumbing: engine, session factory, declarative ``Base`` and ``get_session``.

ORM tables live in ``app/models.py`` and stay separate from the Pydantic API models
in ``app/schemas/``. Tables are created with ``Base.metadata.create_all`` at startup
(see the lifespan in ``app/main.py``); there is no migration framework yet.

Every SQLite connection loads the sqlite-vec extension, because the chunk vector index
(a ``vec0`` virtual table, see ``app/search_index.py``) lives in the same database file
and triggers on ``evidence_chunks`` write to it.
"""

import logging
import os
import platform
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import DateTime, Engine, create_engine, event, text
from sqlalchemy.engine import Dialect, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.config import DEFAULT_EMBEDDING_DIM

logger = logging.getLogger(__name__)

ROSETTA_WARNING = (
    "This is an x86_64 (Intel) Python running under Rosetta on an Apple Silicon Mac. "
    "Loading sqlite-vec can crash it with 'Fatal Python error: Illegal instruction'. "
    "Use a native arm64 Python 3.12 (python.org or Homebrew) and recreate the venv; "
    "see backend/README.md."
)


class Base(DeclarativeBase):
    """Declarative base for every ORM table."""


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on any backend.

    SQLite has no timezone support, so values are normalised to UTC and stored
    naive, then re-tagged as UTC on load. Naive inputs are rejected.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use a timezone-aware value")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def running_under_rosetta() -> bool:
    """True for an x86_64 Python on an arm64 Mac. Under Rosetta platform.machine()
    reports the process architecture (x86_64) but the kernel version string is still the
    host's (...RELEASE_ARM64_...)."""
    return (
        sys.platform == "darwin"
        and platform.machine() == "x86_64"
        and "ARM64" in os.uname().version
    )


def _load_sqlite_vec(dbapi_connection: Any, _connection_record: Any) -> None:
    from app.search_index import load_sqlite_vec

    load_sqlite_vec(dbapi_connection)


def make_engine(database_url: str) -> Engine:
    """Create an engine. For SQLite: create the file's parent dir and enable foreign keys."""
    url = make_url(database_url)
    kwargs: dict[str, Any] = {}
    is_sqlite = url.get_backend_name() == "sqlite"
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        database = url.database
        if database and database != ":memory:" and not database.startswith("file:"):
            Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        else:
            # In-memory: share one connection so every session sees the same DB.
            kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **kwargs)
    if is_sqlite:
        if running_under_rosetta():
            # Warn before the first connection loads sqlite-vec: that may crash the process.
            logger.warning(ROSETTA_WARNING)
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        event.listen(engine, "connect", _load_sqlite_vec)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine, embedding_dim: int = DEFAULT_EMBEDDING_DIM) -> None:
    """Create all tables, plus the vector/keyword indexes for chunks, and mark cases that
    already hold records as ``ready``.

    Imports the models so they register on ``Base.metadata``. ``embedding_dim`` sizes the
    sqlite-vec table; see ``app.search_index.ensure_search_index`` for what happens when it
    differs from an existing index.
    """
    from app import models  # noqa: F401
    from app.search_index import ensure_search_index

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _ensure_evidence_record_media_columns(connection)
        _ensure_evidence_chunk_embedding_coverage_column(connection)
        ensure_search_index(connection, embedding_dim)
        # Cases imported before ``store_records`` set ``ready`` were left ``empty``.
        connection.execute(
            text(
                "UPDATE cases SET import_status = 'ready' WHERE import_status = 'empty' "
                "AND EXISTS (SELECT 1 FROM evidence_records r WHERE r.case_id = cases.case_id)"
            )
        )


def _ensure_evidence_record_media_columns(connection) -> None:
    """Add BLA-68 media columns on existing SQLite DBs created before those fields existed."""
    if connection.dialect.name != "sqlite":
        return
    existing = {row[1] for row in connection.execute(text("PRAGMA table_info(evidence_records)"))}
    for name, ddl in (
        ("modality", "ALTER TABLE evidence_records ADD COLUMN modality VARCHAR(16)"),
        ("media_path", "ALTER TABLE evidence_records ADD COLUMN media_path TEXT"),
        ("content_type", "ALTER TABLE evidence_records ADD COLUMN content_type VARCHAR(128)"),
    ):
        if name not in existing:
            connection.execute(text(ddl))


def _ensure_evidence_chunk_embedding_coverage_column(connection) -> None:
    """Add BLA-69 ``embedding_coverage`` on existing SQLite DBs (create_all does not ALTER)."""
    if connection.dialect.name != "sqlite":
        return
    existing = {row[1] for row in connection.execute(text("PRAGMA table_info(evidence_chunks)"))}
    if "embedding_coverage" not in existing:
        connection.execute(text("ALTER TABLE evidence_chunks ADD COLUMN embedding_coverage JSON"))


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to this app's engine."""
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session
