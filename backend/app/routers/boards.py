"""Board endpoints: create, list, read, pin/unpin and annotate (BLA-31).

Every response body is ``InvestigationBoard`` contract JSON (``to_contract_json``).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.boards import (
    BoardConflictError,
    BoardNotFoundError,
    InvalidBoardChangeError,
    RevisionLookup,
    create_board,
    get_board,
    list_boards,
    list_revisions,
    no_saved_revisions,
    update_board,
)
from app.contracts import InvestigationBoard
from app.deps import CaseDep, SessionDep
from app.schemas.boards import BoardChange, BoardCreate

router = APIRouter(prefix="/cases/{case_id}/boards", tags=["boards"])

BOARD = {"model": InvestigationBoard}


def get_revision_lookup() -> RevisionLookup:
    """Dependency: how boards find saved analysis revisions.

    BLA-28 (save and reload revisions) should return its lookup here. Until then no
    revision is known, so pins are rejected with a 422 that says so.
    """
    return no_saved_revisions


LookupDep = Annotated[RevisionLookup, Depends(get_revision_lookup)]


def _json(board: InvestigationBoard, code: int = status.HTTP_200_OK) -> JSONResponse:
    return JSONResponse(board.to_contract_json(), status_code=code)


def _errors(error: Exception) -> HTTPException:
    if isinstance(error, BoardNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail="Board not found")
    if isinstance(error, BoardConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(error))
    assert isinstance(error, InvalidBoardChangeError)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=error.errors)


@router.post("", status_code=status.HTTP_201_CREATED, responses={201: BOARD})
def create_case_board(
    body: BoardCreate, case: CaseDep, session: SessionDep, lookup: LookupDep
) -> Any:
    """Create an empty board (revision 1)."""
    try:
        board = create_board(
            session,
            case.case_id,
            body.title,
            active_analysis_id=body.active_analysis_id,
            lookup=lookup,
        )
    except InvalidBoardChangeError as error:
        raise _errors(error) from error
    return _json(board, status.HTTP_201_CREATED)


@router.get("", responses={200: {"model": list[InvestigationBoard]}})
def list_case_boards(case: CaseDep, session: SessionDep) -> Any:
    """The latest snapshot of every board in the case, most recently updated first."""
    return JSONResponse([board.to_contract_json() for board in list_boards(session, case.case_id)])


@router.get("/{board_id}", responses={200: BOARD})
def get_case_board(board_id: str, case: CaseDep, session: SessionDep) -> Any:
    """The board's latest snapshot."""
    try:
        return _json(get_board(session, case.case_id, board_id))
    except BoardNotFoundError as error:
        raise _errors(error) from error


@router.get("/{board_id}/revisions", responses={200: {"model": list[InvestigationBoard]}})
def list_board_revisions(board_id: str, case: CaseDep, session: SessionDep) -> Any:
    """Every snapshot of the board, oldest first."""
    try:
        boards = list_revisions(session, case.case_id, board_id)
    except BoardNotFoundError as error:
        raise _errors(error) from error
    return JSONResponse([board.to_contract_json() for board in boards])


@router.get("/{board_id}/revisions/{revision}", responses={200: BOARD})
def get_board_revision(board_id: str, revision: int, case: CaseDep, session: SessionDep) -> Any:
    """One snapshot of the board."""
    try:
        return _json(get_board(session, case.case_id, board_id, revision))
    except BoardNotFoundError as error:
        raise _errors(error) from error


@router.patch("/{board_id}", responses={200: BOARD})
def change_case_board(
    board_id: str, body: BoardChange, case: CaseDep, session: SessionDep, lookup: LookupDep
) -> Any:
    """Unpin, pin and add notes, then append one snapshot (revision + 1).

    A pin copies the finding's evidence and status from its saved revision. 404 for an
    unknown board, 409 if ``expected_revision`` is stale or another save won, 422 if the
    change is invalid (nothing is saved). A change that alters nothing returns the
    current snapshot unchanged.
    """
    try:
        result = update_board(session, case.case_id, board_id, body, lookup=lookup)
    except (BoardNotFoundError, BoardConflictError, InvalidBoardChangeError) as error:
        raise _errors(error) from error
    return _json(result.board)
