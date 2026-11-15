"""Case report export (BLA-49): a board or one analysis revision as Markdown or JSON.

Assembled from stored data only (``app.reports``); no model is called. Markdown is
rendered by ``app.report_markdown``; JSON is the versioned ``CaseReport`` shape
(``report_version``). Both are downloads (``Content-Disposition: attachment``).
"""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response

from app.analyses import RevisionNotFoundError
from app.boards import BoardNotFoundError
from app.db import utcnow
from app.deps import CaseDep, SessionDep
from app.report_markdown import render_markdown
from app.reports import CaseReport, analysis_report, board_report

router = APIRouter(prefix="/cases/{case_id}", tags=["reports"])

ReportFormat = Literal["md", "json"]
FormatQuery = Annotated[ReportFormat, Query(description="`md` (default) or `json`")]
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The report as Markdown (`format=md`) or JSON (`format=json`).",
        "content": {
            "text/markdown": {"schema": {"type": "string"}},
            "application/json": {"schema": {"$ref": "#/components/schemas/CaseReport"}},
        },
    },
}


def get_report_clock() -> Callable[[], datetime]:
    """Dependency: the clock for ``generated_at``. Tests override it for fixed output."""
    return utcnow


ClockDep = Annotated[Callable[[], datetime], Depends(get_report_clock)]


def _filename(*parts: str, extension: str) -> str:
    stem = "-".join(_UNSAFE_FILENAME.sub("_", part).strip("._")[:80] for part in parts)
    return f"blanc-report-{stem}.{extension}"


def _respond(report: CaseReport, fmt: ReportFormat, *parts: str) -> Response:
    filename = _filename(*parts, extension=fmt)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if fmt == "json":
        return JSONResponse(report.model_dump(mode="json"), headers=headers)
    return Response(
        render_markdown(report), media_type="text/markdown; charset=utf-8", headers=headers
    )


@router.get(
    "/boards/{board_id}/export",
    response_model=CaseReport,
    response_class=Response,
    responses=_RESPONSES,
)
def export_board(
    board_id: str,
    case: CaseDep,
    session: SessionDep,
    clock: ClockDep,
    format: FormatQuery = "md",
    revision: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    """Export a board (latest snapshot, or ``revision``): its pinned findings with their
    sources, the revisions they come from, excluded evidence, investigator notes and
    limitations. 404 for an unknown board or a board of another case."""
    try:
        report = board_report(session, case, board_id, revision=revision, now=clock())
    except BoardNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Board not found") from error
    return _respond(report, format, case.case_id, board_id)


@router.get(
    "/analyses/{analysis_id}/export",
    response_model=CaseReport,
    response_class=Response,
    responses=_RESPONSES,
)
def export_analysis(
    analysis_id: str,
    case: CaseDep,
    session: SessionDep,
    clock: ClockDep,
    format: FormatQuery = "md",
) -> Response:
    """Export one saved revision: answer, every claim, entity, relationship and event with
    their sources, excluded evidence and limitations. 404 if it is not a revision of this
    case."""
    try:
        report = analysis_report(session, case, analysis_id, now=clock())
    except RevisionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Analysis not found") from error
    return _respond(report, format, case.case_id, analysis_id)
