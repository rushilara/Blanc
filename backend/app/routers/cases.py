"""Case endpoints: create, list and select a case."""

from fastapi import APIRouter, status
from sqlalchemy import select

from app.deps import CaseDep, SessionDep
from app.models import Case
from app.schemas.cases import CaseCreate, CaseRead

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_case(body: CaseCreate, session: SessionDep) -> CaseRead:
    case = Case(
        title=body.title,
        description=body.description,
        source_corpus_metadata=body.source_corpus_metadata,
    )
    session.add(case)
    session.commit()
    return CaseRead.model_validate(case)


@router.get("")
def list_cases(session: SessionDep) -> list[CaseRead]:
    """All cases, newest first."""
    rows = session.scalars(select(Case).order_by(Case.created_at.desc(), Case.case_id.desc()))
    return [CaseRead.model_validate(case) for case in rows]


@router.get("/{case_id}")
def get_case(case: CaseDep) -> CaseRead:
    return CaseRead.model_validate(case)
