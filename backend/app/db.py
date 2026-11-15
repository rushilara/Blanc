"""Database plumbing: engine, session factory, declarative ``Base`` and ``get_session``.

ORM tables live in ``app/models.py`` and stay separate from the Pydantic API models
in ``app/schemas/``. Tables are created with ``Base.metadata.create_all`` at startup
(see the lifespan in ``app/main.py``); there is no migration framework yet.

Every SQLite connection loads the sqlite-vec extension, because the chunk vector index
(a ``vec0`` virtual table, see ``app/search_index.py``) lives in the same database file
and triggers on ``evidence_chunks`` write to it.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import Dialect, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.config import DEFAULT_EMBEDDING_DIM


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
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        event.listen(engine, "connect", _load_sqlite_vec)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine, embedding_dim: int = DEFAULT_EMBEDDING_DIM) -> None:
    """Create all tables, plus the vector/keyword indexes for chunks.

    Imports the models so they register on ``Base.metadata``. ``embedding_dim`` sizes the
    sqlite-vec table; see ``app.search_index.ensure_search_index`` for what happens when it
    differs from an existing index.
    """
    from app import models  # noqa: F401
    from app.search_index import ensure_search_index

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        ensure_search_index(connection, embedding_dim)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to this app's engine."""
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session
