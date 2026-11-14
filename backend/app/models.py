"""ORM tables. Keep these separate from the Pydantic API/contract models.

Case isolation rule: every table that holds evidence or anything derived from it
(records, chunks, analyses, boards, ...) must have a NOT NULL ``case_id`` foreign
key to ``cases.case_id``, and every query must be scoped by ``case_id``. See
``backend/README.md``.
"""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Enum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

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
