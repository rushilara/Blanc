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
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
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
    # Media (BLA-68): relative path under the case dir; None for text-only records.
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
        if self.modality is not None:
            data["modality"] = self.modality
        if self.media_path is not None:
            data["media_path"] = self.media_path
        if self.content_type is not None:
            data["content_type"] = self.content_type
        if self.record_metadata is not None:
            data["metadata"] = self.record_metadata
        return EvidenceRecord.model_validate(data)


class EvidenceChunk(Base):
    """A chunk of one record, with its embedding metadata.

    For text records, ``text == content[char_start:char_end]``. For image/PDF records
    (empty ``content``), ``text`` is the lexical FTS sidecar (filename / parent email
    headers); offsets span that sidecar string, not the binary. The source of truth for
    the vec0 / FTS5 indexes (``app/search_index.py``), keyed by ``chunk_id`` and cleaned
    up by a delete trigger on this table. The unique ``(case_id, record_id, chunk_index)``
    index doubles as the ``case_id`` index.
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


class AnalysisRevisionRow(Base):
    """One saved ``AnalysisRevision`` (BLA-28). Immutable: a trigger rejects any UPDATE, and
    a follow-up, exclusion or restore is a new row linked by ``parent_analysis_id``.

    ``revision`` holds the contract JSON exactly as returned; the other columns copy what
    lists and lineage queries need. The ``(case_id, analysis_id)`` primary key leads with
    ``case_id`` (it doubles as the ``case_id`` index), and the parent foreign key includes
    ``case_id``, so a revision can only descend from a revision of the same case.
    """

    __tablename__ = "analysis_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "parent_analysis_id"],
            ["analysis_revisions.case_id", "analysis_revisions.analysis_id"],
        ),
        Index("ix_analysis_revisions_created_at", "case_id", "created_at"),
    )

    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.case_id", ondelete="CASCADE"), primary_key=True
    )
    analysis_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_analysis_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revision: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


event.listen(
    AnalysisRevisionRow.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER analysis_revisions_immutable BEFORE UPDATE ON analysis_revisions "
        "BEGIN SELECT RAISE(ABORT, 'analysis revisions are immutable'); END"
    ).execute_if(dialect="sqlite"),
)


class RevisionRestoreRow(Base):
    """Marks a saved revision as a restore (BLA-35): it reuses the findings and evidence
    state of ``restored_from_analysis_id``, an earlier revision in its lineage. Kept in its
    own table so ``analysis_revisions`` needs no new column. Insert-only, like revisions."""

    __tablename__ = "revision_restores"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "analysis_id"],
            ["analysis_revisions.case_id", "analysis_revisions.analysis_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["case_id", "restored_from_analysis_id"],
            ["analysis_revisions.case_id", "analysis_revisions.analysis_id"],
        ),
    )

    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.case_id", ondelete="CASCADE"), primary_key=True
    )
    analysis_id: Mapped[str] = mapped_column(Text, primary_key=True)
    restored_from_analysis_id: Mapped[str] = mapped_column(Text, nullable=False)
    restored_record_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)


event.listen(
    RevisionRestoreRow.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER revision_restores_immutable BEFORE UPDATE ON revision_restores "
        "BEGIN SELECT RAISE(ABORT, 'revision restores are immutable'); END"
    ).execute_if(dialect="sqlite"),
)


class BoardSnapshotRow(Base):
    """One append-only ``InvestigationBoard`` snapshot (BLA-31).

    Every save inserts a new row with ``revision`` one higher; rows are never updated
    (a trigger rejects any UPDATE). ``snapshot`` holds the contract JSON exactly as
    returned. The ``(case_id, board_id, revision)`` primary key leads with ``case_id``, so
    it doubles as the required ``case_id`` index, and a second save racing for the same
    revision number fails instead of overwriting.
    """

    __tablename__ = "board_snapshots"
    __table_args__ = (CheckConstraint("revision >= 1", name="ck_board_snapshots_revision"),)

    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.case_id", ondelete="CASCADE"), primary_key=True
    )
    board_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


event.listen(
    BoardSnapshotRow.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER board_snapshots_append_only BEFORE UPDATE ON board_snapshots "
        "BEGIN SELECT RAISE(ABORT, 'board snapshots are append-only'); END"
    ).execute_if(dialect="sqlite"),
)
