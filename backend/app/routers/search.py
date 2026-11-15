"""Debug search endpoint: inspect hybrid retrieval for one case (BLA-13)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.deps import CaseDep, EmbedderDep, SessionDep
from app.retrieval import RetrievalConfig, RetrievalResult, retrieve

router = APIRouter(prefix="/cases/{case_id}", tags=["search"])


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(max_length=4000)]
    # Overrides RETRIEVAL_MAX_RECORDS for this call.
    limit: Annotated[int | None, Field(ge=1, le=200)] = None


def get_retrieval_config(request: Request) -> RetrievalConfig:
    return request.app.state.retrieval_config


@router.post("/search")
def search_case(
    body: SearchRequest,
    case: CaseDep,
    session: SessionDep,
    embedder: EmbedderDep,
    config: Annotated[RetrievalConfig, Depends(get_retrieval_config)],
) -> RetrievalResult:
    """Run hybrid retrieval for ``question`` in this case and return the evidence window
    (records, exact excerpts, locators, fused scores and per-retriever ranks). Read-only."""
    return retrieve(session, case.case_id, body.question, embedder, limit=body.limit, config=config)
