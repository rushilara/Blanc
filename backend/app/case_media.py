"""Case-local media files for image and PDF evidence (BLA-68).

Bytes live on disk under ``{cases_dir}/{case_id}/media/{record_id}.{ext}``. SQLite holds
the relative ``media_path`` (``media/...``), content hash, modality and content type—not
the blob. Embedding and vision analysis are later tickets (BLA-69+).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

IMAGE_TYPES = {
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
}
PDF_TYPE = (".pdf", "application/pdf", "pdf")

MEDIA_SUFFIXES = tuple(IMAGE_TYPES) + (PDF_TYPE[0],)

# MIME types we promote from email attachments into their own records.
EMAIL_IMAGE_TYPES = {"image/png", "image/jpeg"}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PDF_MAGIC = b"%PDF-"
_PDF_PAGE = re.compile(rb"/Type\s*/Page(?!\s*s)")


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
    """File extension for a known image MIME type, preferring the attachment filename."""
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in IMAGE_TYPES and IMAGE_TYPES[suffix][0] == content_type:
            return suffix
    if content_type == "image/png":
        return ".png"
    if content_type == "image/jpeg":
        return ".jpg"
    if content_type == "application/pdf":
        return ".pdf"
    return None


def verify_media_bytes(data: bytes, content_type: str) -> None:
    """Raise ``MediaStoreError`` if ``data`` does not match ``content_type``."""
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


def pdf_page_count(data: bytes) -> int | None:
    """Best-effort page count from PDF markers; None when unknown."""
    if not data.startswith(_PDF_MAGIC):
        return None
    count = len(_PDF_PAGE.findall(data))
    return count or None


def pdf_locator(data: bytes) -> str:
    """Locator for a whole PDF: ``pages 1-N`` when countable, else ``entire file``."""
    pages = pdf_page_count(data)
    if pages is None:
        return "entire file"
    if pages == 1:
        return "page 1"
    return f"pages 1-{pages}"


def store_media_bytes(
    media_dir: Path, record_id: str, data: bytes, extension: str
) -> tuple[str, str]:
    """Write ``data`` under ``media_dir`` as ``{record_id}{extension}``.

    Returns ``(media_path, sha256)`` where ``media_path`` is relative to the case directory
    (``media/{record_id}.ext``). Same path and hash is a no-op; a different hash at the
    same path raises ``MediaStoreError`` so changed bytes are detected.
    """
    ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
    if not record_id.strip():
        raise MediaStoreError("record_id must be nonempty")
    digest = hashlib.sha256(data).hexdigest()
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / f"{record_id}{ext}"
    if dest.exists():
        existing = hashlib.sha256(dest.read_bytes()).hexdigest()
        if existing != digest:
            raise MediaStoreError(
                f"{dest.name} already exists with different bytes "
                f"(stored sha256 {existing[:12]}…, new {digest[:12]}…)"
            )
        return f"media/{record_id}{ext}", digest
    dest.write_bytes(data)
    return f"media/{record_id}{ext}", digest
