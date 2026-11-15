"""Original image files behind a record, served from a local evidence folder.

Some records point at an image on the original machine, e.g. a Chrome download row whose
``target_path`` is ``C:\\Users\\Steve\\Documents\\Misc\\flightbookings.PNG``. If that file
was extracted to the investigator's local evidence folder (``EVIDENCE_ROOT``), the
workspace can show it next to the record.

Local-first rules:

- Files are only read from under ``EVIDENCE_ROOT/{case_id}/``. Paths are normalised,
  ``..`` and absolute paths are refused, and the resolved path (symlinks followed) must
  stay inside that case folder. A second case with the same original path does not see
  the first case's bytes.
- An ``artifact_manifest.jsonl`` in the case folder (one JSON object per line with
  ``source_path``, ``extracted_file`` and ``sha256``) maps original paths to extracted
  files. Without an entry, the file is looked up at its original relative path.
- An entry may also list ``record_ids``: records shown to be about that file by
  evidence recorded outside the app (e.g. a download row whose file was later moved,
  matched by exact name and size). A record's own paths are tried first; a listed
  ``record_ids`` link is used only when none of them resolves.
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


@dataclass(frozen=True)
class _Entry:
    source_path: str
    extracted_file: str
    sha256: str | None


def _manifest(root: Path) -> tuple[dict[str, _Entry], dict[str, _Entry]]:
    """Entries by case-folded ``source_path``, and by listed ``record_ids``. The first
    entry naming a record wins, so a record never maps to two files."""
    path = root / MANIFEST_NAME
    by_path: dict[str, _Entry] = {}
    by_record: dict[str, _Entry] = {}
    if not path.is_file():
        return by_path, by_record
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        source, extracted = item.get("source_path"), item.get("extracted_file")
        if not (isinstance(source, str) and isinstance(extracted, str)):
            continue
        normalized = normalize_source_path(source)
        if not normalized:
            continue
        sha = item.get("sha256") if isinstance(item.get("sha256"), str) else None
        entry = _Entry(normalized, extracted, sha)
        by_path[normalized.casefold()] = entry
        record_ids = item.get("record_ids")
        if isinstance(record_ids, list):
            for record_id in record_ids:
                if isinstance(record_id, str) and record_id and record_id not in by_record:
                    by_record[record_id] = entry
    return by_path, by_record


def _matches_type(data: bytes, media_type: str) -> bool:
    if media_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if media_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if media_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if media_type == "application/pdf":
        return data.startswith(b"%PDF-")
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


def resolve_case_media(
    record: EvidenceRecord, cases_dir: Path | None, case_id: str
) -> MediaFile | None:
    """The file Blanc itself imported for an image/PDF record (BLA-68), from
    ``CASES_DIR/{case_id}/{media_path}``, checked against the SHA-256 recorded at import
    (``metadata.media_sha256``). None if the record has no stored media or the file is
    not there (the caller then falls back to ``EVIDENCE_ROOT``). Raises
    ``MediaIntegrityError`` if the file is the wrong type, too large or altered."""
    if cases_dir is None or record.media_path is None or record.content_type is None:
        return None
    if not case_id.strip():
        return None
    try:
        root = (Path(cases_dir).expanduser() / case_id).resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None
    path = _inside(root, record.media_path)
    if path is None:
        return None
    if path.stat().st_size > MAX_MEDIA_BYTES:
        raise MediaIntegrityError(f"{record.media_path} is larger than {MAX_MEDIA_BYTES} bytes")
    data = path.read_bytes()
    if not _matches_type(data, record.content_type):
        raise MediaIntegrityError(f"{record.media_path} is not a valid {record.content_type} file")
    digest = hashlib.sha256(data).hexdigest()
    expected = (record.metadata or {}).get("media_sha256")
    if isinstance(expected, str) and digest != expected.lower():
        raise MediaIntegrityError(f"{record.media_path} does not match its recorded SHA-256")
    return MediaFile(
        data,
        record.content_type,
        record.source_file,
        digest,
        verified=isinstance(expected, str),
    )


def resolve_media(record: EvidenceRecord, evidence_root: Path | None, case_id: str) -> MediaFile:
    """The local image for ``record`` under ``EVIDENCE_ROOT/{case_id}/``. Raises
    ``MediaNotFoundError`` or ``MediaIntegrityError``."""
    if evidence_root is None:
        raise MediaNotFoundError("no local evidence folder is configured (EVIDENCE_ROOT)")
    if not case_id.strip():
        raise MediaNotFoundError("case_id is required to resolve local evidence")
    try:
        base = evidence_root.expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise MediaNotFoundError("the local evidence folder does not exist") from exc
    # Case folder only: never read another case's extracted files or manifest.
    try:
        root = (base / case_id).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise MediaNotFoundError("this record's image is not in the local evidence folder") from exc
    if not root.is_relative_to(base) or not root.is_dir():
        raise MediaNotFoundError("this record's image is not in the local evidence folder")
    by_path, by_record = _manifest(root)

    candidates: list[_Entry] = []
    for source_path in media_source_paths(record):
        candidates.append(
            by_path.get(source_path.casefold(), _Entry(source_path, source_path, None))
        )
    linked = by_record.get(record.record_id)
    if linked is not None and Path(linked.source_path).suffix.lower() in IMAGE_TYPES:
        candidates.append(linked)

    for entry in candidates:
        media = _load(root, entry)
        if media is not None:
            return media
    raise MediaNotFoundError("this record's image is not in the local evidence folder")


def _load(root: Path, entry: _Entry) -> MediaFile | None:
    """The checked image for ``entry``, or None if its file is not in ``root``."""
    media_type = IMAGE_TYPES[Path(entry.source_path).suffix.lower()]
    path = _inside(root, entry.extracted_file)
    if path is None:
        return None
    if path.stat().st_size > MAX_MEDIA_BYTES:
        raise MediaIntegrityError(f"{entry.source_path} is larger than {MAX_MEDIA_BYTES} bytes")
    data = path.read_bytes()
    if not _matches_type(data, media_type):
        raise MediaIntegrityError(f"{entry.source_path} is not a valid {media_type} file")
    digest = hashlib.sha256(data).hexdigest()
    if entry.sha256 is not None and digest != entry.sha256.lower():
        raise MediaIntegrityError(f"{entry.source_path} does not match its recorded SHA-256")
    return MediaFile(data, media_type, entry.source_path, digest, verified=entry.sha256 is not None)
