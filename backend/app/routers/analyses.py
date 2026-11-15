"""Saved analysis revisions: history, one revision, its lineage (BLA-28), and restoring
excluded records by reusing an earlier evidence state (BLA-35).

Revisions are created by the analyze, follow-up and exclude endpoints through
``app.analyses.save_revision``; restore creates one through ``restore_records``. A revision
is returned as ``AnalysisRevision`` contract JSON, exactly as saved.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.analyses import (
    InvalidRestoreError,
    NoPriorStateError,
    RevisionConflictError,
    RevisionNotFoundError,
    RevisionSummary,
    get_revision,
    list_revisions,
    restore_records,
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
    excluded_record_ids: list[str]  # the evidence state of this revision
    restored_from_analysis_id: str | None  # set when this revision is a restore

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


class RestoreRequest(BaseModel):
    """Body of ``POST .../restore``: the excluded records to bring back."""

    model_config = ConfigDict(extra="forbid")

    record_ids: Annotated[
        list[Annotated[str, StringConstraints(min_length=1)]], Field(min_length=1, max_length=200)
    ]


@router.post(
    "/{analysis_id}/restore",
    status_code=status.HTTP_201_CREATED,
    responses={201: {"model": AnalysisRevision}},
)
def restore_analysis_records(
    analysis_id: str, body: RestoreRequest, case: CaseDep, session: SessionDep
) -> Any:
    """Bring excluded records back by reusing the nearest earlier revision (same question)
    whose exclusions are exactly what remains. Saves that revision's findings as a new
    child of ``analysis_id``; nothing is deleted or rewritten. Returns the new revision.

    404 for an unknown revision; 422 if a record is not excluded here; 409 if no earlier
    revision had that evidence state (the analysis has to be rerun instead).
    """
    try:
        result = restore_records(session, case.case_id, analysis_id, body.record_ids)
    except RevisionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Analysis not found") from error
    except InvalidRestoreError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    except (NoPriorStateError, RevisionConflictError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    return JSONResponse(
        result.revision.to_contract_json(),
        status_code=status.HTTP_201_CREATED,
        headers={"X-Restored-From": result.restored_from.analysis_id},
    )
