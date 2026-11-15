"""Source record endpoints: the evidence drawer's record + exact excerpt lookup (BLA-59)."""

import re
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.deps import CaseDep, SessionDep
from app.media import (
    MediaIntegrityError,
    MediaNotFoundError,
    resolve_case_media,
    resolve_media,
)
from app.records import (
    ChunkNotFoundError,
    InvalidExcerptError,
    RecordNotFoundError,
    file_context,
    get_record_with_excerpt,
    page_records,
)
from app.schemas.records import ExcerptRead, FileContextRead, RecordPage, RecordRead

router = APIRouter(prefix="/cases/{case_id}/records", tags=["records"])

MAX_PAGE = 200
MAX_CONTEXT = 200


@router.get("")
def list_case_records(
    case: CaseDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecordPage:
    """The case's records ordered by ``(source_file, record_id)``, paginated."""
    items, total = page_records(session, case.case_id, limit=limit, offset=offset)
    return RecordPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{record_id}")
def get_case_record(
    record_id: str,
    case: CaseDep,
    session: SessionDep,
    chunk_id: Annotated[str | None, Query(min_length=1)] = None,
    char_start: Annotated[int | None, Query(ge=0)] = None,
    char_end: Annotated[int | None, Query(ge=0)] = None,
) -> RecordRead:
    """One record of the case, plus the exact excerpt for ``chunk_id`` or
    ``char_start``/``char_end`` when given. 404 if the record (or chunk) is not in this
    case; 422 for a malformed excerpt request."""
    try:
        found = get_record_with_excerpt(
            session,
            case.case_id,
            record_id,
            chunk_id=chunk_id,
            char_start=char_start,
            char_end=char_end,
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Record not found") from exc
    except ChunkNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chunk not found") from exc
    except InvalidExcerptError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    excerpt = found.excerpt
    return RecordRead(
        record=found.record,
        excerpt=ExcerptRead(
            text=excerpt.text,
            char_start=excerpt.char_start,
            char_end=excerpt.char_end,
            chunk_id=excerpt.chunk_id,
        )
        if excerpt is not None
        else None,
    )


@router.get(
    "/{record_id}/media",
    response_class=Response,
    responses={200: {"content": {"image/*": {}, "application/pdf": {}}}, 404: {}, 409: {}},
)
def get_case_record_media(
    record_id: str, case: CaseDep, session: SessionDep, request: Request
) -> Response:
    """The original image (or PDF) for this record: the copy Blanc stored at import
    (``CASES_DIR/{case_id}/{media_path}``, BLA-70) if there is one, else the image it
    points at in the local evidence folder (``EVIDENCE_ROOT/{case_id}/``). 404 if the
    record is not in this case or its file is not available locally; 409 if the local
    file fails its type, size or SHA-256 check.
    Never fetches anything remote. See ``app/media.py``."""
    try:
        record = get_record_with_excerpt(session, case.case_id, record_id).record
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Record not found") from exc
    try:
        # Media Blanc imported itself (CASES_DIR) first, then the external evidence folder.
        media = resolve_case_media(
            record, getattr(request.app.state, "cases_dir", None), case.case_id
        ) or resolve_media(
            record, getattr(request.app.state, "evidence_root", None), case.case_id
        )
    except MediaNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MediaIntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # Header values are latin-1: percent-encode the path. Omit on CR/LF so a malicious
    # target_path cannot split response headers.
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-SHA256": media.sha256,
        "X-Hash-Verified": "true" if media.verified else "false",
    }
    if "\r" not in media.source_path and "\n" not in media.source_path:
        headers["X-Source-Path"] = quote(media.source_path, safe="/")
    if media.media_type == "application/pdf":
        # Never rendered inline under the sandboxed CSP: offered as a download instead.
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", record.record_id)
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}.pdf"'
    return Response(content=media.data, media_type=media.media_type, headers=headers)


@router.get("/{record_id}/context")
def get_case_record_context(
    record_id: str,
    case: CaseDep,
    session: SessionDep,
    before: Annotated[int, Query(ge=0, le=MAX_CONTEXT)] = 40,
    after: Annotated[int, Query(ge=0, le=MAX_CONTEXT)] = 40,
) -> FileContextRead:
    """The record in its original file: up to ``before`` / ``after`` records of the same
    ``source_file`` in this case around it, in file order (see ``app.records.file_context``).
    404 if the record is not in this case."""
    try:
        found = file_context(session, case.case_id, record_id, before=before, after=after)
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Record not found") from exc
    return FileContextRead(
        source_file=found.source_file,
        total=found.total,
        index=found.index,
        start=found.start,
        ordered_by=found.ordered_by,
        items=found.items,
    )
