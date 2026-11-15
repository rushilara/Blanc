"""Saved analysis revisions: history, one revision, and its lineage (BLA-28).

Revisions are created by the analyze, follow-up, exclude and restore endpoints through
``app.analyses.save_revision``; these routes only read them. A revision is returned as
``AnalysisRevision`` contract JSON, exactly as saved.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.analyses import (
    RevisionNotFoundError,
    RevisionSummary,
    get_revision,
    list_revisions,
    revision_lineage,
)
from app.contracts import AnalysisRevision
from app.deps import CaseDep, SessionDep

router = APIRouter(prefix="/cases/{case_id}/analyses", tags=["analyses"])


class RevisionSummaryRead(BaseModel):
    """One revision in a history list: identity and lineage, no findings."""

    analysis_id: str
    parent_analysis_id: str | None
    question: str
    needs_review: bool
    created_at: datetime

    @classmethod
    def of(cls, summary: RevisionSummary) -> "RevisionSummaryRead":
        return cls(**summary.__dict__)


@router.get("")
def list_case_analyses(case: CaseDep, session: SessionDep) -> list[RevisionSummaryRead]:
    """The case's saved revisions, newest first."""
    return [RevisionSummaryRead.of(s) for s in list_revisions(session, case.case_id)]


@router.get("/{analysis_id}", responses={200: {"model": AnalysisRevision}})
def get_case_analysis(analysis_id: str, case: CaseDep, session: SessionDep) -> Any:
    """One saved revision, exactly as stored. 404 if it is not a revision of this case."""
    try:
        revision = get_revision(session, case.case_id, analysis_id)
    except RevisionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Analysis not found") from error
    return JSONResponse(revision.to_contract_json())


@router.get("/{analysis_id}/lineage")
def get_analysis_lineage(
    analysis_id: str, case: CaseDep, session: SessionDep
) -> list[RevisionSummaryRead]:
    """The revision and its ancestors, oldest first: the path from the first question to it."""
    try:
        chain = revision_lineage(session, case.case_id, analysis_id)
    except RevisionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Analysis not found") from error
    return [RevisionSummaryRead.of(s) for s in chain]
