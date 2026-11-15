"""Shared FastAPI dependencies, including case scoping.

Any route under ``/cases/{case_id}/...`` must resolve the case through
``require_case`` (or ``CaseDep``) so an unknown case 404s before any other query
runs, and must then filter every query by the resolved ``case.case_id``.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.embeddings import Embedder
from app.models import Case

SessionDep = Annotated[Session, Depends(get_session)]


def get_case_or_404(session: Session, case_id: str) -> Case:
    """Load a case by ID or raise HTTP 404. For use outside path-param dependencies."""
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


def require_case(case_id: str, session: SessionDep) -> Case:
    """Dependency: resolve the ``{case_id}`` path parameter to an existing case or 404."""
    return get_case_or_404(session, case_id)


CaseDep = Annotated[Case, Depends(require_case)]


def get_embedder(request: Request) -> Embedder:
    """Dependency: the app's embedder (``GeminiEmbedder``; tests may swap in a fake by
    setting ``app.state.embedder``). Pass it to ``app.evidence_store.store_records``."""
    return request.app.state.embedder


EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
