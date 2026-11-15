"""Case-local media files for image and PDF evidence (BLA-68 / BLA-69).

Bytes live on disk under ``{cases_dir}/{case_id}/media/{record_id}.{ext}``. SQLite holds
the relative ``media_path`` (``media/...``), content hash, modality and content type—not
the blob. Multimodal embedding (BLA-69) reads these bytes; vision analysis is BLA-70+.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IMAGE_TYPES = {
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
}
PDF_TYPE = (".pdf", "application/pdf", "pdf")

MEDIA_SUFFIXES = tuple(IMAGE_TYPES) + (PDF_TYPE[0],)

# MIME types we promote from email attachments into their own records.
EMAIL_IMAGE_TYPES = {"image/png", "image/jpeg"}

# Each media file is read whole into memory; refuse anything larger.
MAX_MEDIA_BYTES = 25 * 1024 * 1024

# gemini-embedding-2 accepts one PDF per request, up to ~6 pages.
GEMINI_PDF_MAX_PAGES = 6

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PDF_MAGIC = b"%PDF-"


class MediaStoreError(ValueError):
    """A media file could not be written or verified under the case directory."""


def case_media_dir(cases_dir: Path, case_id: str) -> Path:
    """``{cases_dir}/{case_id}/media`` — created by ``store_media_bytes`` when needed."""
    return Path(cases_dir) / case_id / "media"


def media_type_for_suffix(suffix: str) -> tuple[str, str] | None:
    """``(content_type, modality)`` for a supported media suffix, else None."""
    key = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
    if key in IMAGE_TYPES:
        return IMAGE_TYPES[key]
    if key == PDF_TYPE[0]:
        return PDF_TYPE[1], PDF_TYPE[2]
    return None


def extension_for_content_type(content_type: str, filename: str | None = None) -> str | None:
    """Canonical on-disk extension for a known media MIME type.

    Always maps ``image/jpeg`` to ``.jpg`` (never ``.jpeg``) so ``media_path`` matches
    the contract pattern ``media/rec_<16 hex>.(png|jpg|pdf)``. ``filename`` is ignored
    for the suffix — MIME type is authoritative.
    """
    del filename  # MIME type owns the stored extension; see media_path schema pattern
    if content_type == "image/png":
        return ".png"
    if content_type == "image/jpeg":
        return ".jpg"
    if content_type == "application/pdf":
        return ".pdf"
    return None


def verify_media_bytes(data: bytes, content_type: str) -> None:
    """Raise ``MediaStoreError`` if ``data`` does not match ``content_type``."""
    if len(data) > MAX_MEDIA_BYTES:
        raise MediaStoreError(f"media file is {len(data)} bytes; limit is {MAX_MEDIA_BYTES} bytes")
    if content_type == "image/png":
        if not data.startswith(_PNG_MAGIC):
            raise MediaStoreError("bytes are not a valid PNG file")
    elif content_type == "image/jpeg":
        if not data.startswith(_JPEG_MAGIC):
            raise MediaStoreError("bytes are not a valid JPEG file")
    elif content_type == "application/pdf":
        if not data.startswith(_PDF_MAGIC):
            raise MediaStoreError("bytes are not a valid PDF file")
    else:
        raise MediaStoreError(f"unsupported media content type {content_type!r}")


def pdf_locator(data: bytes) -> str:
    """Locator for a whole PDF file.

    Always ``entire file``: page counts must not feed ``record_id`` (unstable IDs if a
    parser disagrees with another pass). Long PDFs stay one ingest record; truncation to
    Gemini's page limit happens at embed time via ``truncate_pdf_for_embedding``.
    """
    del data  # bytes checked at verify time; locator does not depend on them
    return "entire file"


@dataclass(frozen=True)
class PdfEmbeddingPrep:
    """Result of preparing a PDF for ``embed_media``.

    ``truncated`` is True when pages beyond ``embedding_pages`` were dropped. ``page_count``
    is the source page count when parsing succeeded; both page fields are None when the
    PDF could not be parsed and the original bytes are embedded unchanged.
    """

    data: bytes
    truncated: bool
    page_count: int | None = None
    embedding_pages: int | None = None

    def coverage_metadata(self) -> dict[str, Any] | None:
        """Chunk ``embedding_coverage`` keys when the embed does not cover the whole PDF.

        Returned for the PDF's ``EvidenceChunk`` only — never written onto
        ``evidence_records.metadata`` (stored evidence stays immutable).
        """
        if not self.truncated:
            return None
        meta: dict[str, Any] = {"embedding_truncated": True}
        if self.embedding_pages is not None:
            meta["embedding_pages"] = self.embedding_pages
        if self.page_count is not None:
            meta["embedding_page_count"] = self.page_count
        return meta


def truncate_pdf_for_embedding(
    data: bytes, *, max_pages: int = GEMINI_PDF_MAX_PAGES
) -> PdfEmbeddingPrep:
    """Return a PDF of at most ``max_pages`` (Gemini embedding-2 limit), else ``data``.

    Uses pypdf. Corrupt or unreadable PDFs are returned unchanged so the embed call can
    surface the provider error; page count is only used for truncation, never for IDs.
    Logs a warning when pages beyond ``max_pages`` are dropped or when parse errors force
    returning the original bytes.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - declared in requirements.txt
        raise MediaStoreError("pypdf is required to truncate PDFs for embedding") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        total = len(reader.pages)
        if total <= max_pages:
            return PdfEmbeddingPrep(
                data=data, truncated=False, page_count=total, embedding_pages=total
            )
        writer = PdfWriter()
        for index in range(max_pages):
            writer.add_page(reader.pages[index])
        out = io.BytesIO()
        writer.write(out)
        logger.warning(
            "PDF truncated for embedding: keeping first %s of %s pages "
            "(pages %s+ are not searchable by meaning until a later ticket)",
            max_pages,
            total,
            max_pages + 1,
        )
        return PdfEmbeddingPrep(
            data=out.getvalue(),
            truncated=True,
            page_count=total,
            embedding_pages=max_pages,
        )
    except Exception as exc:
        logger.warning(
            "PDF truncation failed (%s); embedding original bytes unchanged",
            exc,
        )
        return PdfEmbeddingPrep(data=data, truncated=False)


def prepare_media_for_embedding(
    data: bytes, mime_type: str
) -> tuple[bytes, str, dict[str, Any] | None]:
    """Bytes + MIME ready for ``Embedder.embed_media`` (PDF page truncation applied).

    The optional third value is coverage for the PDF chunk's ``embedding_coverage`` when a
    PDF was truncated (``embedding_truncated`` / page counts). Images always return ``None``.
    """
    if mime_type == "application/pdf":
        prep = truncate_pdf_for_embedding(data)
        return prep.data, mime_type, prep.coverage_metadata()
    return data, mime_type, None


def store_media_bytes(
    media_dir: Path, record_id: str, data: bytes, extension: str
) -> tuple[str, str, bool]:
    """Write ``data`` under ``media_dir`` as ``{record_id}{extension}``.

    Returns ``(media_path, sha256, created)`` where ``media_path`` is relative to the case
    directory (``media/{record_id}.ext``) and ``created`` is True when this call wrote a
    new file. Same path and hash is a no-op (``created`` False); a different hash at the
    same path raises ``MediaStoreError`` so changed bytes are detected.

    Writes via a temp file plus ``os.replace`` so a truncated write never becomes the
    destination. Refuses to write through a symlinked destination path.
    """
    ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
    if not record_id.strip():
        raise MediaStoreError("record_id must be nonempty")
    if len(data) > MAX_MEDIA_BYTES:
        raise MediaStoreError(f"media file is {len(data)} bytes; limit is {MAX_MEDIA_BYTES} bytes")
    digest = hashlib.sha256(data).hexdigest()
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / f"{record_id}{ext}"
    if dest.is_symlink():
        raise MediaStoreError(
            f"{dest.name} is a symbolic link; refusing to write through it "
            "(media must stay under the case media directory)"
        )
    if dest.exists():
        existing = hashlib.sha256(dest.read_bytes()).hexdigest()
        if existing != digest:
            raise MediaStoreError(
                f"{dest.name} already exists with different bytes "
                f"(stored sha256 {existing[:12]}…, new {digest[:12]}…)"
            )
        return f"media/{record_id}{ext}", digest, False

    fd, tmp_name = tempfile.mkstemp(prefix=f".{record_id}.", suffix=".tmp", dir=media_dir)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return f"media/{record_id}{ext}", digest, True


def remove_media_files(paths: dict[str, Path]) -> None:
    """Delete media files newly written during a failed or rejected import."""
    for path in paths.values():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
