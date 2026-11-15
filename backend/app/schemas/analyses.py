"""Request body for ``POST /cases/{case_id}/analyses`` (BLA-20).

The response is the ``AnalysisRevision`` contract JSON itself (``to_contract_json``).
``analysis_id`` in the request is the optional prior/parent revision, not the new
server-assigned ID on the response.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.contracts import EvidenceWindow
from app.search_index import to_fts_query

Id = Annotated[str, StringConstraints(min_length=1, max_length=200)]

# Same rule as ``POST /search``: nothing searchable → 422.
EMPTY_QUESTION = "question must contain at least one letter or digit"


class AnalysisCreate(BaseModel):
    """Body of ``POST /cases/{case_id}/analyses``."""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(max_length=4000)]
    # Optional prior revision of the same case; becomes parent_analysis_id on the new one.
    analysis_id: Id | None = None
    # BLA-64. Omitted: the prior revision's window, or "standard" with no prior.
    evidence_window: EvidenceWindow | None = None

    @field_validator("question")
    @classmethod
    def _searchable(cls, value: str) -> str:
        if to_fts_query(value) is None:
            raise ValueError(EMPTY_QUESTION)
        return value


class FollowUpCreate(BaseModel):
    """Body of ``POST /cases/{case_id}/analyses/{analysis_id}/follow-up``."""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(max_length=4000)]
    # Optional board of this case whose topic and pinned findings give the follow-up context.
    board_id: Id | None = None
    # BLA-64. Omitted: the parent revision's window; "standard" overrides an expanded parent.
    evidence_window: EvidenceWindow | None = None

    @field_validator("question")
    @classmethod
    def _searchable(cls, value: str) -> str:
        if to_fts_query(value) is None:
            raise ValueError(EMPTY_QUESTION)
        return value


class RerunCreate(BaseModel):
    """Body of ``POST /cases/{case_id}/analyses/{analysis_id}/rerun`` (BLA-64)."""

    model_config = ConfigDict(extra="forbid")

    # The window to rerun the same question with; must differ from the revision's own.
    evidence_window: EvidenceWindow
