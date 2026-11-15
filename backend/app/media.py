"""Original image files behind a record, served from a local evidence folder.

Some records point at an image on the original machine, e.g. a Chrome download row whose
``target_path`` is ``C:\\Users\\Steve\\Documents\\Misc\\flightbookings.PNG``. If that file
was extracted to the investigator's local evidence folder (``EVIDENCE_ROOT``), the
workspace can show it next to the record.

Local-first rules:

- Files are only read from under ``EVIDENCE_ROOT``. Paths are normalised, ``..`` and
  absolute paths are refused, and the resolved path (symlinks followed) must stay inside
  the root.
- An ``artifact_manifest.jsonl`` in the root (one JSON object per line with
  ``source_path``, ``extracted_file`` and ``sha256``) maps original paths to extracted
  files. Without an entry, the file is looked up at its original relative path.
- Only image types are served, and the file's leading bytes must match its type.
- When the manifest records a SHA-256, the file must match it, so a changed or swapped
  file is refused rather than shown as evidence.
- Nothing is ever fetched from the network; a remote-only image is simply not available.

A record's ``original_sha256`` is the hash of its *source file* (for a download row, the
browser History database), not of the downloaded image, so it is not used here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.contracts import EvidenceRecord

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_MEDIA_BYTES = 25 * 1024 * 1024
MANIFEST_NAME = "artifact_manifest.jsonl"


class MediaNotFoundError(LookupError):
    """The record has no image available in the local evidence folder."""


class MediaIntegrityError(ValueError):
    """A local file exists but is not the expected image (type or hash mismatch)."""


@dataclass(frozen=True)
class MediaFile:
    data: bytes
    media_type: str
    source_path: str  # the original path on the evidence machine, relative
    sha256: str
    verified: bool  # True when the hash matched a manifest entry


def normalize_source_path(raw: str) -> str | None:
    """``C:\\Users\\A\\x.png`` or ``Users/A/x.png`` -> ``Users/A/x.png``; None if unsafe."""
    text = raw.strip().replace("\\", "/")
    text = re.sub(r"^[A-Za-z]:/", "", text).lstrip("/")
    parts = PurePosixPath(text).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


def media_source_paths(record: EvidenceRecord) -> list[str]:
    """Original paths of images this record points at, most specific first."""
    paths: list[str] = []
    try:
        data = json.loads(record.content)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("target_path"), str):
        paths.append(data["target_path"])  # a browser download row's saved file
    paths.append(record.source_file)  # a record extracted from an image file itself
    out: list[str] = []
    for raw in paths:
        normalized = normalize_source_path(raw)
        if normalized and Path(normalized).suffix.lower() in IMAGE_TYPES and normalized not in out:
            out.append(normalized)
    return out


def _manifest(root: Path) -> dict[str, tuple[str, str | None]]:
    """``source_path`` (case-folded) -> (``extracted_file``, ``sha256``)."""
    path = root / MANIFEST_NAME
    if not path.is_file():
        return {}
    entries: dict[str, tuple[str, str | None]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        source, extracted = item.get("source_path"), item.get("extracted_file")
        if isinstance(source, str) and isinstance(extracted, str):
            normalized = normalize_source_path(source)
            sha = item.get("sha256") if isinstance(item.get("sha256"), str) else None
            if normalized:
                entries[normalized.casefold()] = (extracted, sha)
    return entries


def _matches_type(data: bytes, media_type: str) -> bool:
    if media_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if media_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if media_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _inside(root: Path, relative: str) -> Path | None:
    """``root/relative`` resolved, only if it is a regular file still inside ``root``."""
    safe = normalize_source_path(relative)
    if safe is None:
        return None
    try:
        resolved = (root / safe).resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved


def resolve_media(record: EvidenceRecord, evidence_root: Path | None) -> MediaFile:
    """The local image for ``record``. Raises ``MediaNotFoundError`` or
    ``MediaIntegrityError``."""
    if evidence_root is None:
        raise MediaNotFoundError("no local evidence folder is configured (EVIDENCE_ROOT)")
    try:
        root = evidence_root.expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise MediaNotFoundError("the local evidence folder does not exist") from exc
    manifest = _manifest(root)

    for source_path in media_source_paths(record):
        media_type = IMAGE_TYPES[Path(source_path).suffix.lower()]
        extracted, expected = manifest.get(source_path.casefold(), (source_path, None))
        path = _inside(root, extracted)
        if path is None:
            continue
        if path.stat().st_size > MAX_MEDIA_BYTES:
            raise MediaIntegrityError(f"{source_path} is larger than {MAX_MEDIA_BYTES} bytes")
        data = path.read_bytes()
        if not _matches_type(data, media_type):
            raise MediaIntegrityError(f"{source_path} is not a valid {media_type} file")
        digest = hashlib.sha256(data).hexdigest()
        if expected is not None and digest != expected.lower():
            raise MediaIntegrityError(f"{source_path} does not match its recorded SHA-256")
        return MediaFile(data, media_type, source_path, digest, verified=expected is not None)
    raise MediaNotFoundError("this record's image is not in the local evidence folder")
