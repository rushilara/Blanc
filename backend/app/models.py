"""ORM tables. Keep these separate from the Pydantic API/contract models.

Case isolation rule: every table that holds evidence or anything derived from it
(records, chunks, analyses, boards, ...) must have a NOT NULL ``case_id`` foreign
key to ``cases.case_id``, and every query must be scoped by ``case_id``. See
``backend/README.md``.
"""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.contracts import EvidenceRecord
from app.db import Base, UTCDateTime, utcnow
from app.ids import new_id

CASE_ID_PREFIX = "case"


def new_case_id() -> str:
    return new_id(CASE_ID_PREFIX)


class ImportStatus(enum.StrEnum):
    EMPTY = "empty"
    IMPORTING = "importing"
    READY = "ready"
    ERROR = "error"


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="ck_cases_title_not_blank"),
        Index("ix_cases_created_at", "created_at"),
    )

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_case_id)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    import_status: Mapped[ImportStatus] = mapped_column(
        Enum(
            ImportStatus,
            name="import_status",
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ImportStatus.EMPTY,
    )
    source_corpus_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


CHUNK_ID_PREFIX = "chk"


def new_chunk_id() -> str:
    return new_id(CHUNK_ID_PREFIX)


class EvidenceRecordRow(Base):
    """One stored ``EvidenceRecord``. Immutable once stored; keyed by ``(case_id, record_id)``
    because record IDs come from the importer and are only unique within a case.

    The composite primary key's index leads with ``case_id``, so it doubles as the
    required ``case_id`` index.
    """

    __tablename__ = "evidence_records"

    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.case_id", ondelete="CASCADE"), primary_key=True
    )
    record_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_locator: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    original_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # None means the record had no ``metadata`` key (distinct from ``{}``).
    record_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )
    # SHA-256 of the record's canonical contract JSON; detects changed re-imports.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    def to_contract(self) -> EvidenceRecord:
        data: dict[str, Any] = {
            "record_id": self.record_id,
            "case_id": self.case_id,
            "source_file": self.source_file,
            "source_locator": self.source_locator,
            "content": self.content,
            "timestamp": self.timestamp,
            "original_sha256": self.original_sha256,
        }
        if self.timestamp_kind is not None:
            data["timestamp_kind"] = self.timestamp_kind
        if self.record_metadata is not None:
            data["metadata"] = self.record_metadata
        return EvidenceRecord.model_validate(data)


class EvidenceChunk(Base):
    """A slice ``content[char_start:char_end]`` of one record, with its embedding metadata.

    The source of truth for the vec0 / FTS5 indexes (``app/search_index.py``), which are
    keyed by ``chunk_id`` and cleaned up by a delete trigger on this table. The unique
    ``(case_id, record_id, chunk_index)`` index doubles as the ``case_id`` index.
    """

    __tablename__ = "evidence_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "record_id"],
            ["evidence_records.case_id", "evidence_records.record_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("case_id", "record_id", "chunk_index", name="uq_evidence_chunks_index"),
        CheckConstraint(
            "char_start >= 0 AND char_end > char_start", name="ck_evidence_chunks_offsets"
        ),
    )

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_chunk_id)
    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[str] = mapped_column(String(200), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # The model and dimension that produced this chunk's vector; a mismatch with the
    # current embedder means the vector is stale.
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
