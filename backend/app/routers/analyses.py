"""Saved analysis revisions: create (BLA-20), history/lineage (BLA-28), restore (BLA-35).

``POST /cases/{case_id}/analyses`` runs retrieval + validated LLM analysis and saves through
``app.analyses.save_revision``. GET routes reload history; restore reuses an earlier evidence
state. A revision is returned as ``AnalysisRevision`` contract JSON, exactly as saved.
"""

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.analyses import (
    InvalidExclusionError,
    InvalidRestoreError,
    NoPriorStateError,
    RevisionConflictError,
    RevisionNotFoundError,
    RevisionSummary,
    compare_to_parent,
    exclude_records,
    get_revision,
    list_revisions,
    restore_records,
    revision_lineage,
    run_case_analysis,
)
from app.analysis_llm import AnalysisLLMError
from app.contracts import AnalysisRevision, RevisionComparison
from app.deps import AnalysisLLMDep, CaseDep, EmbedderDep, SessionDep
from app.retrieval import ExcludedRecordLeakError, RetrievalConfig
from app.routers.search import get_retrieval_config
from app.schemas.analyses import AnalysisCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases/{case_id}/analyses", tags=["analyses"])

LLM_UNAVAILABLE = "The analysis service is not ready. Please try again later."
RetrievalConfigDep = Annotated[RetrievalConfig, Depends(get_retrieval_config)]


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


@router.post("", status_code=status.HTTP_201_CREATED, responses={201: {"model": AnalysisRevision}})
def create_case_analysis(
    body: AnalysisCreate,
    case: CaseDep,
    session: SessionDep,
    embedder: EmbedderDep,
    llm: AnalysisLLMDep,
    config: RetrievalConfigDep,
) -> Any:
    """Retrieve evidence for ``question``, run structured analysis, save and return the
    revision. Optional ``analysis_id`` is a prior revision of this case (parent context);
    its exclusions are inherited.

    201 with AnalysisRevision contract JSON. 404 unknown case or prior analysis. 422 when
    the question has no letters or digits (same rule as ``POST /search``). 503 when the LLM
    is not configured or the provider fails.
    """
    try:
        revision = run_case_analysis(
            session,
            case.case_id,
            body.question,
            llm,
            embedder,
            retrieval_config=config,
            prior_analysis_id=body.analysis_id,
        )
    except RevisionNotFoundError as error:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Prior analysis not found"
        ) from error
    except AnalysisLLMError as error:
        logger.warning("analysis LLM unavailable for case %s: %s", case.case_id, error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=LLM_UNAVAILABLE) from error
    except ExcludedRecordLeakError:
        logger.exception(
            "excluded record leaked into analysis for case %s; this is a server bug",
            case.case_id,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from None
    return JSONResponse(revision.to_contract_json(), status_code=status.HTTP_201_CREATED)


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


class ExcludeRequest(BaseModel):
    """Body of ``POST .../exclude``: records to exclude, on top of the base's exclusions."""

    model_config = ConfigDict(extra="forbid")

    record_ids: Annotated[
        list[Annotated[str, StringConstraints(min_length=1)]], Field(min_length=1, max_length=200)
    ]


@router.post(
    "/{analysis_id}/exclude",
    status_code=status.HTTP_201_CREATED,
    responses={201: {"model": AnalysisRevision}},
)
def exclude_analysis_records(
    analysis_id: str,
    body: ExcludeRequest,
    case: CaseDep,
    session: SessionDep,
    embedder: EmbedderDep,
    llm: AnalysisLLMDep,
    config: RetrievalConfigDep,
) -> Any:
    """Exclude records and rerun this revision's question on the remaining evidence (BLA-33).

    Retrieval filters every chunk of the excluded records; the new revision is generated
    from permitted evidence only, saved as a child of ``analysis_id`` and returned (201).
    The excluded records stay in the corpus. ``GET .../{new_id}/comparison`` gives the
    support diff against this revision.

    404 for an unknown revision; 422 if a record is not in this case, none is given, or all
    are already excluded; 503 when the LLM is unavailable.
    """
    try:
        revision = exclude_records(
            session,
            case.case_id,
            analysis_id,
            body.record_ids,
            llm,
            embedder,
            retrieval_config=config,
        )
    except RevisionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Analysis not found") from error
    except InvalidExclusionError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    except AnalysisLLMError as error:
        logger.warning("analysis LLM unavailable for case %s: %s", case.case_id, error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=LLM_UNAVAILABLE) from error
    except ExcludedRecordLeakError:
        logger.exception(
            "excluded record leaked into analysis for case %s; this is a server bug",
            case.case_id,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from None
    return JSONResponse(revision.to_contract_json(), status_code=status.HTTP_201_CREATED)


@router.get("/{analysis_id}/comparison", responses={200: {"model": RevisionComparison}})
def get_analysis_comparison(analysis_id: str, case: CaseDep, session: SessionDep) -> Any:
    """How each finding's support changed from this revision's parent to it (BLA-34):
    ``still_supported``, ``weakened``, ``not_supported``, ``newly_supported`` or
    ``unresolved``. Deterministic. 404 if the revision is unknown or has no parent."""
    try:
        comparison = compare_to_parent(session, case.case_id, analysis_id)
    except RevisionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return JSONResponse(comparison.to_contract_json())
