from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import ImportStatus

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CaseCreate(BaseModel):
    """Body of ``POST /cases``. ``case_id``, ``created_at`` and status are server-set."""

    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Annotated[str | None, Field(max_length=5000)] = None
    source_corpus_metadata: dict[str, Any] | None = None


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    title: str
    description: str | None
    created_at: datetime
    import_status: ImportStatus
    source_corpus_metadata: dict[str, Any] | None
