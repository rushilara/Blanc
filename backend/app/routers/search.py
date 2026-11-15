"""Debug search endpoint: inspect hybrid retrieval for one case (BLA-13)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.deps import CaseDep, EmbedderDep, SessionDep
from app.retrieval import RetrievalConfig, RetrievalResult, retrieve
from app.search_index import to_fts_query

router = APIRouter(prefix="/cases/{case_id}", tags=["search"])

EMPTY_QUESTION = "question must contain at least one letter or digit"


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(max_length=4000)]
    # Overrides RETRIEVAL_MAX_RECORDS for this call.
    limit: Annotated[int | None, Field(ge=1, le=200)] = None

    @field_validator("question")
    @classmethod
    def _searchable(cls, value: str) -> str:
        # Same rule retrieve() uses to return an empty result: nothing to search for.
        if to_fts_query(value) is None:
            raise ValueError(EMPTY_QUESTION)
        return value


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
    (records, exact excerpts, locators, fused scores, per-retriever ranks, and the records
    the window skipped). Read-only. An empty or punctuation-only question is a 422."""
    return retrieve(session, case.case_id, body.question, embedder, limit=body.limit, config=config)
