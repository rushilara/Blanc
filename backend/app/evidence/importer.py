"""Import extracted evidence files (TXT, JSON, CSV, EML, PNG/JPEG, PDF) as records.

Case-agnostic by design: nothing here knows about any particular case beyond its
id and optional media directory. One source file can yield many records (one per
message, row or paragraph), and every record keeps the file it came from and a
precise locator inside it.

Locator conventions:
- TXT: `line 12`, `lines 12-15` or `entire file` (line numbers are 1-based).
- JSON: `[3]` for a top-level array, `messages[3]` for an array under a key,
  `$` for a single top-level object (indexes are 0-based, as in JSON).
- CSV: `line 4`, or `lines 4-6` for a row whose quoted cells span several lines.
  These are physical line numbers in the file, so the header is line 1 and
  blank lines are counted.
- EML: `headers` for the message's header block (sender, recipients, date,
  subject), then `body` for a single-part message, or `part 1`, `part 1.2` for
  the parts of a multipart message (IMAP section numbers). A text attachment is a
  part like any other. PNG/JPEG attachments become their own media records (same
  part locator) with bytes under the case media directory; other binary
  attachments stay listed in the headers record's `metadata.attachments`, never
  in its content. In a `multipart/alternative`, only the preferred text version
  is kept (plain text over HTML) so the same words are not imported twice; when
  `promote_images` is on, image leaves under a non-preferred alternative (e.g.
  `multipart/related` with cid inline PNGs) are still walked into media records.
  A forwarded message (`message/rfc822`) at section 2 is read like the file's
  own message: `part 2 headers`, then `part 2.1`, `part 2.2`, ..., with its own
  Date as their timestamp. An empty `.eml` file gives no records; a non-empty
  one with none of the usual email headers is an error.
- PNG/JPEG/PDF: one record per file. Images and PDFs both use `entire file`
  as the locator (page counts wait on a real PDF parser). Bytes are written
  under `media_dir` as `media/{record_id}.{ext}` relative to the case directory
  (BLA-68). Without `media_dir`, loose media files are skipped in a folder
  import (same as other unsupported formats) and fail clearly for a single
  file; PNG/JPEG email attachments stay metadata-only. Text records are
  unchanged; other unsupported binary still fails clearly (single file) or is
  skipped in a folder import. PDF email attachments stay metadata-only.

Source root: every `source_file` is the path relative to the case's source
root, and record IDs are derived from that path. Importing a whole export,
one of its subfolders or a single file therefore gives the same IDs, as long
as the same source root is used.

Symbolic links are never followed. A link can point outside the source root, or
at an answer key under a harmless-looking name, so linked files and anything
reached through a linked folder are skipped and reported.

Timestamps are copied as found in the source. This module never invents or
reinterprets one; normalization happens later in the pipeline.
"""

import csv
import email
import email.policy
import fnmatch
import hashlib
import io
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import ValidationError

from app.case_media import (
    EMAIL_IMAGE_TYPES,
    MAX_MEDIA_BYTES,
    MEDIA_SUFFIXES,
    MediaStoreError,
    extension_for_content_type,
    media_type_for_suffix,
    pdf_locator,
    remove_media_files,
    store_media_bytes,
    verify_media_bytes,
)
from app.contracts import RecordModality
from app.evidence.records import EvidenceRecord, TimestampKind, stable_record_id

SUPPORTED_SUFFIXES = (".txt", ".json", ".csv", ".eml", *MEDIA_SUFFIXES)

TxtMode = Literal["paragraph", "line", "file"]

# Field or column names (lowercase) that carry the record's text.
TEXT_KEYS = ("content", "text", "body", "message", "msg", "value")

# Field or column names (lowercase) that carry a timestamp, with what kind of
# time each name implies. Generic names say nothing about the kind.
TIME_KEYS: dict[str, TimestampKind] = {
    "timestamp": "unknown",
    "datetime": "unknown",
    "date": "unknown",
    "time": "unknown",
    "ts": "unknown",
    "sent_at": "sent",
    "sent": "sent",
    "date_sent": "sent",
    "received_at": "received",
    "received": "received",
    "created_at": "created",
    "created": "created",
    "modified_at": "modified",
    "modified": "modified",
    "updated_at": "modified",
}

# Keys under which a JSON object commonly holds its list of records.
LIST_KEYS = ("messages", "records", "items", "entries", "events", "rows", "data", "results")

# Scenario spoilers and answer keys must never enter the searchable corpus.
DEFAULT_SKIP_PATTERNS = (
    "*answer_key*",
    "*answer-key*",
    "*answerkey*",
    "*teacher*guide*",
    "*solution*",
    "*case_brief*",
    "*case-brief*",
)

_NATIVE_REQUIRED = {"record_id", "source_file", "source_locator", "content"}

_LEADING_TIMESTAMP = re.compile(
    r"^\s*\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?"
)


class EvidenceImportError(ValueError):
    """A file could not be turned into evidence records."""


class UnsupportedFormatError(EvidenceImportError):
    """The file's format is not one the importer handles."""


@dataclass(frozen=True)
class ImportProblem:
    """A file that was skipped on purpose or failed to import."""

    source_file: str
    reason: str


@dataclass
class ImportSink:
    """Mutable side-channel for media writes and soft warnings during one file import."""

    written_media: dict[str, Path] = field(default_factory=dict)
    warnings: list[ImportProblem] = field(default_factory=list)


@dataclass
class ImportResult:
    records: list[EvidenceRecord] = field(default_factory=list)
    skipped: list[ImportProblem] = field(default_factory=list)
    errors: list[ImportProblem] = field(default_factory=list)
    warnings: list[ImportProblem] = field(default_factory=list)
    # record_id -> absolute path for media files this import newly created (not no-ops).
    written_media: dict[str, Path] = field(default_factory=dict)


def import_path(
    path: str | Path,
    case_id: str,
    *,
    source_root: str | Path | None = None,
    media_dir: str | Path | None = None,
    txt_mode: TxtMode = "paragraph",
    skip_patterns: Iterable[str] = DEFAULT_SKIP_PATTERNS,
    strict: bool = False,
) -> ImportResult:
    """Import one file or every supported file under a folder.

    `source_root` is the top folder of the case export. It defaults to `path`
    when `path` is a folder and is required when `path` is a single file, so
    record IDs never depend on how the import was started.

    `media_dir` is where PNG/JPEG/PDF bytes are written (`{record_id}.ext`). With
    `--store` the CLI defaults it under `CASES_DIR`. Without it, loose media is
    skipped (folder) or rejected as unsupported (single file), and email images
    stay metadata-only.

    Files are visited in sorted order so results are deterministic. A file that
    fails is reported in `errors` and never half-imported; with `strict=True`
    the first failure raises instead. Listed in `skipped`, not `errors`: symbolic
    links, hidden files and folders, anything under a name matching
    `skip_patterns`, and, when importing a folder, files in a format the importer
    does not handle (including media when `media_dir` is absent).
    """
    _require_case_id(case_id)
    target = Path(path)
    if not target.exists():
        raise EvidenceImportError(f"Path does not exist: {target}")

    is_folder = target.is_dir()
    base = _relative_to_root(target, source_root)
    if is_folder:
        files = [
            (candidate, (base / candidate.relative_to(target)).as_posix())
            for candidate in sorted(target.rglob("*"))
            if candidate.is_file() or candidate.is_symlink()
        ]
    else:
        files = [(target, base.as_posix())]

    patterns = tuple(pattern.lower() for pattern in skip_patterns)
    result = ImportResult()
    seen_ids: set[str] = set()
    for file_path, source_file in files:
        parts = PurePosixPath(source_file).parts
        if is_folder and _reached_through_symlink(target, file_path):
            result.skipped.append(ImportProblem(source_file, "symbolic link (not followed)"))
            continue
        if any(part.startswith(".") for part in parts):
            result.skipped.append(ImportProblem(source_file, "hidden file or folder"))
            continue
        if any(fnmatch.fnmatch(part.lower(), pattern) for part in parts for pattern in patterns):
            result.skipped.append(
                ImportProblem(source_file, "matches a skip pattern (answer key or case brief)")
            )
            continue
        sink = ImportSink()
        try:
            records = import_file(
                file_path,
                case_id,
                source_file=source_file,
                media_dir=media_dir,
                txt_mode=txt_mode,
                sink=sink,
            )
            clash = next((record for record in records if record.record_id in seen_ids), None)
            if clash is not None:
                raise EvidenceImportError(
                    f"{source_file} repeats record_id {clash.record_id}, which another file "
                    "already provided; none of this file's records were imported"
                )
        except (EvidenceImportError, ValidationError) as error:
            # Media may already be on disk for a part that succeeded before a later
            # part failed; drop those orphans so a failed file never leaves bytes behind.
            remove_media_files(sink.written_media)
            if is_folder and isinstance(error, UnsupportedFormatError):
                shown = file_path.suffix.lower() or "(no extension)"
                result.skipped.append(ImportProblem(source_file, f"unsupported format {shown}"))
                continue
            if strict:
                # Earlier files already landed in result.written_media; drop them too so a
                # hard abort never leaves bytes for a case the CLI will not store.
                remove_media_files(result.written_media)
                result.written_media.clear()
                raise
            reason = (
                str(error)
                if isinstance(error, EvidenceImportError)
                else f"invalid evidence record: {error}"
            )
            result.errors.append(ImportProblem(source_file, reason))
            continue
        except Exception:
            # Unexpected failure (not EvidenceImportError/ValidationError): same orphan
            # risk for this file and any earlier successful media writes.
            remove_media_files(sink.written_media)
            remove_media_files(result.written_media)
            result.written_media.clear()
            raise
        seen_ids.update(record.record_id for record in records)
        result.records.extend(records)
        result.written_media.update(sink.written_media)
        result.warnings.extend(sink.warnings)

    return result


def import_file(
    path: str | Path,
    case_id: str,
    *,
    source_root: str | Path | None = None,
    source_file: str | None = None,
    media_dir: str | Path | None = None,
    txt_mode: TxtMode = "paragraph",
    sink: ImportSink | None = None,
) -> list[EvidenceRecord]:
    """Import a single file. Raises `EvidenceImportError` with a clear reason on failure.

    Give either `source_root` (the top folder of the case export, from which the
    file's relative path is taken) or `source_file` (that relative path itself).
    One of them is required: record IDs are derived from the path, so it must not
    depend on which folder the import happened to start from.

    `media_dir` is required to promote PNG/JPEG/PDF into media records. Without it,
    loose media raises ``UnsupportedFormatError`` (folder imports skip those files)
    and PNG/JPEG email attachments stay metadata-only.
    """
    _require_case_id(case_id)
    file_path = Path(path)
    if file_path.is_symlink():
        raise EvidenceImportError(
            f"{file_path.name} is a symbolic link. Links are not followed, so evidence cannot "
            "be pulled in from outside the source root or from an excluded file; import the "
            "real file instead."
        )
    if source_file is not None:
        source = source_file
    elif source_root is not None:
        source = _relative_to_root(file_path, source_root).as_posix()
    else:
        raise EvidenceImportError(
            f"Importing {file_path.name} needs source_root (or source_file), so record IDs "
            "do not depend on how the import was started"
        )
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        shown = suffix or "(no extension)"
        raise UnsupportedFormatError(
            f"Unsupported format {shown} for {source}. "
            f"Supported formats: {', '.join(SUPPORTED_SUFFIXES)}."
        )
    if suffix in MEDIA_SUFFIXES:
        try:
            size = file_path.stat().st_size
        except OSError as error:
            raise EvidenceImportError(f"Could not read {source}: {error}") from error
        if size > MAX_MEDIA_BYTES:
            raise EvidenceImportError(
                f"{source} is {size} bytes; media limit is {MAX_MEDIA_BYTES} bytes"
            )
    try:
        raw = file_path.read_bytes()
    except OSError as error:
        raise EvidenceImportError(f"Could not read {source}: {error}") from error

    sha256 = hashlib.sha256(raw).hexdigest()
    media_root = Path(media_dir) if media_dir is not None else None
    out = sink if sink is not None else ImportSink()
    if suffix == ".eml":
        builder = _RecordBuilder(
            case_id=case_id,
            source_file=source,
            sha256=sha256,
            encoding="utf-8",
            media_dir=media_root,
            sink=out,
        )
        records = list(_from_eml(raw, builder))
        _reject_duplicate_ids(records)
        return records
    if suffix in MEDIA_SUFFIXES:
        if media_root is None:
            shown = suffix or "(no extension)"
            raise UnsupportedFormatError(
                f"Unsupported format {shown} for {source} without media_dir "
                "(pass --media-dir or use --store so image/PDF bytes can be kept under the case)"
            )
        builder = _RecordBuilder(
            case_id=case_id,
            source_file=source,
            sha256=sha256,
            encoding="utf-8",
            media_dir=media_root,
            sink=out,
        )
        records = list(_from_media_file(raw, suffix, builder))
        _reject_duplicate_ids(records)
        return records
    text, encoding = _decode(raw)
    builder = _RecordBuilder(
        case_id=case_id,
        source_file=source,
        sha256=sha256,
        encoding=encoding,
        media_dir=media_root,
        sink=out,
    )

    if suffix == ".txt":
        records = list(_from_txt(text, builder, txt_mode))
    elif suffix == ".json":
        records = list(_from_json(text, builder))
    else:
        records = list(_from_csv(text, builder))

    _reject_duplicate_ids(records)
    return records


def _reached_through_symlink(root: Path, path: Path) -> bool:
    """True if `path`, or any folder between `root` and it, is a symbolic link."""
    current = path
    while current != root and current != current.parent:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _relative_to_root(target: Path, source_root: str | Path | None) -> PurePosixPath:
    """Path of `target` relative to the source root, which must contain it."""
    if source_root is None:
        if target.is_dir():
            return PurePosixPath(".")
        raise EvidenceImportError(
            f"Importing the single file {target.name} needs source_root, so record IDs "
            "do not depend on how the import was started"
        )
    root = Path(source_root)
    if not root.is_dir():
        raise EvidenceImportError(f"source_root is not a folder: {root}")
    try:
        relative = target.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise EvidenceImportError(f"{target} is not inside source_root {root}") from error
    return PurePosixPath(relative.as_posix())


# --- shared helpers -----------------------------------------------------------


@dataclass(frozen=True)
class _RecordBuilder:
    case_id: str
    source_file: str
    sha256: str
    encoding: str
    media_dir: Path | None = None
    sink: ImportSink = field(default_factory=ImportSink)

    def build(
        self,
        *,
        locator: str,
        content: str,
        timestamp: str | None = None,
        timestamp_kind: TimestampKind = "unknown",
        metadata: Mapping[str, Any] | None = None,
        modality: RecordModality | None = None,
        media_path: str | None = None,
        content_type: str | None = None,
    ) -> EvidenceRecord:
        details = dict(metadata or {})
        if self.encoding != "utf-8":
            details.setdefault("source_encoding", self.encoding)
        extras: dict[str, Any] = {}
        if modality is not None:
            extras["modality"] = modality
        if media_path is not None:
            extras["media_path"] = media_path
        if content_type is not None:
            extras["content_type"] = content_type
        return EvidenceRecord(
            record_id=stable_record_id(self.case_id, self.source_file, locator),
            case_id=self.case_id,
            source_file=self.source_file,
            source_locator=locator,
            content=content,
            timestamp=timestamp,
            timestamp_kind=timestamp_kind if timestamp is not None else "unknown",
            original_sha256=self.sha256,
            metadata=details,
            **extras,
        )

    def require_media_dir(self, what: str) -> Path:
        if self.media_dir is None:
            raise EvidenceImportError(
                f"Importing {what} needs media_dir so bytes can be stored under the case "
                f"(e.g. data/cases/{self.case_id}/media)"
            )
        return self.media_dir


def _require_case_id(case_id: str) -> None:
    if not isinstance(case_id, str) or not case_id.strip():
        raise EvidenceImportError("case_id must be a nonempty string")


def _decode(raw: bytes) -> tuple[str, str]:
    """Decode as UTF-8, falling back to Windows-1252 for legacy exports."""
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def _reject_duplicate_ids(records: Iterable[EvidenceRecord]) -> None:
    seen: set[str] = set()
    for record in records:
        if record.record_id in seen:
            raise EvidenceImportError(
                f"Duplicate record_id {record.record_id} ({record.source_file}, "
                f"{record.source_locator})"
            )
        seen.add(record.record_id)


def _timestamp_text(value: Any) -> tuple[str | None, dict[str, Any]]:
    """Return the timestamp exactly as found, as text, plus a note if it was numeric."""
    if value is None or isinstance(value, bool):
        return None, {}
    if isinstance(value, int | float):
        return str(value), {"timestamp_raw_type": "number"}
    text = str(value).strip()
    return (text or None), {}


def _find_key(keys: Iterable[str], wanted: Iterable[str]) -> str | None:
    """First key whose lowercase form is in `wanted`, honoring the order of `wanted`."""
    by_lower: dict[str, str] = {}
    for key in keys:
        if isinstance(key, str):
            by_lower.setdefault(key.strip().lower(), key)
    for name in wanted:
        if name in by_lower:
            return by_lower[name]
    return None


# --- TXT ----------------------------------------------------------------------


def _from_txt(text: str, builder: _RecordBuilder, mode: TxtMode) -> Iterator[EvidenceRecord]:
    lines = text.splitlines()
    if mode == "file":
        content = text.strip()
        if content:
            yield _txt_record(builder, "entire file", content)
        return
    if mode == "line":
        for number, line in enumerate(lines, start=1):
            if line.strip():
                yield _txt_record(builder, f"line {number}", line.strip())
        return
    if mode != "paragraph":
        raise EvidenceImportError(f"Unknown txt_mode {mode!r}; use paragraph, line or file")

    block: list[str] = []
    start = 0
    for number, line in enumerate([*lines, ""], start=1):
        if line.strip():
            if not block:
                start = number
            block.append(line.rstrip())
            continue
        if block:
            end = number - 1
            locator = f"line {start}" if start == end else f"lines {start}-{end}"
            yield _txt_record(builder, locator, "\n".join(block).strip())
            block = []


def _txt_record(builder: _RecordBuilder, locator: str, content: str) -> EvidenceRecord:
    match = _LEADING_TIMESTAMP.match(content)
    return builder.build(
        locator=locator,
        content=content,
        timestamp=match.group(1) if match else None,
    )


# --- JSON ---------------------------------------------------------------------


def _from_json(text: str, builder: _RecordBuilder) -> Iterator[EvidenceRecord]:
    if not text.strip():
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise EvidenceImportError(
            f"{builder.source_file} is not valid JSON: {error.msg} "
            f"(line {error.lineno}, column {error.colno})"
        ) from error

    if isinstance(data, list):
        for index, item in enumerate(data):
            yield from _json_item(item, f"[{index}]", builder)
        return
    if isinstance(data, dict):
        list_key = _find_key(
            (key for key, value in data.items() if isinstance(value, list)), LIST_KEYS
        )
        if list_key is not None:
            for index, item in enumerate(data[list_key]):
                yield from _json_item(item, f"{list_key}[{index}]", builder)
            return
        yield from _json_item(data, "$", builder)
        return
    yield from _json_item(data, "$", builder)


def _json_item(item: Any, locator: str, builder: _RecordBuilder) -> Iterator[EvidenceRecord]:
    if item is None:
        return
    if isinstance(item, dict):
        if _NATIVE_REQUIRED <= item.keys():
            yield _native_record(item, locator, builder)
            return
        text_key = _find_key(item.keys(), TEXT_KEYS)
        text_value = item.get(text_key) if text_key is not None else None
        if isinstance(text_value, str) and text_value.strip():
            content = text_value.strip()
            used = {text_key}
        else:
            content = json.dumps(item, sort_keys=True, ensure_ascii=False)
            used = set()
        time_key = _find_key((key for key in item if key not in used), TIME_KEYS)
        timestamp, notes = _timestamp_text(item.get(time_key)) if time_key else (None, {})
        if time_key is not None and timestamp is not None:
            used.add(time_key)
        metadata = {key: value for key, value in item.items() if key not in used}
        metadata.update(notes)
        yield builder.build(
            locator=locator,
            content=content,
            timestamp=timestamp,
            timestamp_kind=TIME_KEYS[time_key.strip().lower()] if time_key else "unknown",
            metadata=metadata,
        )
        return
    content = item.strip() if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
    if content:
        yield builder.build(locator=locator, content=content)


def _native_record(
    item: Mapping[str, Any], locator: str, builder: _RecordBuilder
) -> EvidenceRecord:
    """Accept an item that is already shaped like an EvidenceRecord (a prior export)."""
    declared_case = item.get("case_id", builder.case_id)
    if declared_case != builder.case_id:
        raise EvidenceImportError(
            f"{builder.source_file} {locator}: record belongs to case {declared_case!r}, "
            f"not {builder.case_id!r}"
        )
    try:
        return EvidenceRecord.model_validate({**item, "case_id": builder.case_id})
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
            for issue in error.errors()
        )
        raise EvidenceImportError(
            f"{builder.source_file} {locator}: invalid evidence record ({problems})"
        ) from error


# --- CSV ----------------------------------------------------------------------


def _from_csv(text: str, builder: _RecordBuilder) -> Iterator[EvidenceRecord]:
    if not text.strip():
        return
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            text[:4096], delimiters=",;\t|"
        )
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect, restkey="_extra")
    try:
        fieldnames = [name for name in (reader.fieldnames or []) if name is not None]
        if not any(name.strip() for name in fieldnames):
            raise EvidenceImportError(f"{builder.source_file} has no header row")
        text_key = _find_key(fieldnames, TEXT_KEYS)
        time_key = _find_key((name for name in fieldnames if name != text_key), TIME_KEYS)
        rows = iter(reader)
        while True:
            try:
                row = next(rows)
            except StopIteration:
                break
            # DictReader skips blank lines without yielding them, so count physical
            # lines: `line_num` is the last line of this row, and a quoted cell that
            # contains newlines makes the row start that many lines earlier.
            end_line = reader.line_num
            start_line = end_line - _newlines_in(row)
            locator = (
                f"line {start_line}" if start_line == end_line else f"lines {start_line}-{end_line}"
            )
            cells = {
                key: value.strip()
                for key, value in row.items()
                if isinstance(key, str) and isinstance(value, str) and value.strip()
            }
            extra = row.get("_extra")
            if isinstance(extra, list) and any(cell.strip() for cell in extra):
                cells["_extra"] = " | ".join(cell.strip() for cell in extra)
            if not cells:
                continue
            used: set[str] = set()
            if text_key is not None and text_key in cells:
                content = cells[text_key]
                used.add(text_key)
            else:
                content = "; ".join(f"{key}: {value}" for key, value in cells.items())
            timestamp = cells.get(time_key) if time_key is not None else None
            if timestamp is not None:
                used.add(time_key)
            yield builder.build(
                locator=locator,
                content=content,
                timestamp=timestamp,
                timestamp_kind=TIME_KEYS[time_key.strip().lower()] if time_key else "unknown",
                metadata={key: value for key, value in cells.items() if key not in used},
            )
    except csv.Error as error:
        raise EvidenceImportError(f"{builder.source_file} is not valid CSV: {error}") from error


def _newlines_in(row: Mapping[Any, Any]) -> int:
    """How many line breaks sit inside the cells of one CSV row."""
    total = 0
    for value in row.values():
        cells = value if isinstance(value, list) else [value]
        total += sum(cell.count("\n") for cell in cells if isinstance(cell, str))
    return total


# --- media (PNG / JPEG / PDF) -------------------------------------------------


def _from_media_file(raw: bytes, suffix: str, builder: _RecordBuilder) -> Iterator[EvidenceRecord]:
    """One record for a loose image or PDF; bytes written under ``media_dir``."""
    typed = media_type_for_suffix(suffix)
    if typed is None:
        raise UnsupportedFormatError(f"Unsupported media format {suffix}")
    content_type, modality = typed
    try:
        verify_media_bytes(raw, content_type)
    except MediaStoreError as error:
        raise EvidenceImportError(f"{builder.source_file}: {error}") from error
    locator = pdf_locator(raw) if modality == "pdf" else "entire file"
    yield _media_record(
        builder,
        locator=locator,
        data=raw,
        extension=suffix if suffix != ".jpeg" else ".jpg",
        content_type=content_type,
        modality=modality,  # type: ignore[arg-type]
        filename=Path(builder.source_file).name,
        metadata={},
    )


def _media_record(
    builder: _RecordBuilder,
    *,
    locator: str,
    data: bytes,
    extension: str,
    content_type: str,
    modality: RecordModality,
    filename: str | None,
    metadata: Mapping[str, Any],
    timestamp: str | None = None,
    timestamp_kind: TimestampKind = "unknown",
) -> EvidenceRecord:
    """Write bytes to the case media store and build the EvidenceRecord."""
    media_dir = builder.require_media_dir(builder.source_file)
    record_id = stable_record_id(builder.case_id, builder.source_file, locator)
    ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
    try:
        verify_media_bytes(data, content_type)
        media_path, media_sha, created = store_media_bytes(media_dir, record_id, data, extension)
    except MediaStoreError as error:
        raise EvidenceImportError(f"{builder.source_file} {locator}: {error}") from error
    dest = media_dir / f"{record_id}{ext}"
    if created:
        builder.sink.written_media[record_id] = dest
    details = dict(metadata)
    details["media_sha256"] = media_sha
    if filename:
        details["filename"] = filename
    try:
        return builder.build(
            locator=locator,
            content="",  # no text chunks until multimodal embedding (BLA-69)
            timestamp=timestamp,
            timestamp_kind=timestamp_kind,
            metadata=details,
            modality=modality,
            media_path=media_path,
            content_type=content_type,
        )
    except Exception:
        # build() can fail after bytes are on disk (e.g. schema ValidationError); drop the
        # orphan so a metadata-only fallback or failed file never keeps a row-less file.
        if created:
            builder.sink.written_media.pop(record_id, None)
            dest.unlink(missing_ok=True)
        raise


# --- EML ----------------------------------------------------------------------

# Headers copied into the `headers` record, in this order, when present.
EML_HEADERS = (
    "From",
    "Sender",
    "Reply-To",
    "To",
    "Cc",
    "Bcc",
    "Date",
    "Subject",
    "Message-ID",
    "In-Reply-To",
    "References",
)
_EML_REQUIRED_ANY = ("From", "To", "Date", "Subject", "Message-ID")


# How deep forwarded messages may nest before the file is rejected as malformed.
_EML_MAX_NESTING = 20


def _from_eml(raw: bytes, builder: _RecordBuilder) -> Iterator[EvidenceRecord]:
    """The message's records (see the module docstring). All or nothing: any part that
    cannot be decoded fails the whole file. An empty file gives no records."""
    name = builder.source_file
    if not raw.strip():
        return
    try:
        message = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as error:  # the parser is lenient; this is a last resort
        raise EvidenceImportError(f"{name} could not be parsed as an email: {error}") from error
    if not isinstance(message, EmailMessage) or not any(
        message.get(header) for header in _EML_REQUIRED_ANY
    ):
        raise EvidenceImportError(
            f"{name} does not look like an email message: it has none of the headers "
            f"{', '.join(_EML_REQUIRED_ANY)}"
        )
    yield from _message_records(message, None, builder, depth=0)


def _message_records(
    message: EmailMessage, section: str | None, builder: _RecordBuilder, *, depth: int
) -> Iterator[EvidenceRecord]:
    """Records for one message: its headers, then its text parts in order, with any
    forwarded message expanded in place. ``section`` is None for the file's own message,
    else the IMAP section of the ``message/rfc822`` part holding it (``2``, ``1.3``)."""
    name = builder.source_file
    if depth > _EML_MAX_NESTING:
        raise EvidenceImportError(
            f"{name} nests forwarded messages more than {_EML_MAX_NESTING} deep; "
            "none of this file's records were imported"
        )
    headers, date, base = _message_headers(message, name, section)

    entries: list[tuple[str, str, EmailMessage]] = []
    attachments: list[dict[str, Any]] = []
    # IMAP numbering: a forwarded single-part message's body is section S.1; a multipart
    # one's parts are S.1, S.2, ...; the file's own single-part body is "body".
    body_section = section if section is None or message.is_multipart() else f"{section}.1"
    _collect_parts(
        message,
        body_section,
        entries,
        attachments,
        promote_images=builder.media_dir is not None,
    )

    headers_locator = "headers" if section is None else f"part {section} headers"
    if headers:
        details = {**base, "content_type": message.get_content_type()}
        if attachments:
            details["attachments"] = attachments
        yield builder.build(
            locator=headers_locator,
            content="\n".join(f"{header}: {value}" for header, value in headers.items()),
            timestamp=date,
            timestamp_kind="sent",
            metadata=details,
        )
    for kind, where, part in entries:
        if kind == "message":
            inner = _decoded(part, name, f"part {where}")
            if not isinstance(inner, EmailMessage):
                raise EvidenceImportError(
                    f"{name} part {where} is not a readable forwarded message; none of this "
                    "file's records were imported"
                )
            yield from _message_records(inner, where, builder, depth=depth + 1)
            continue
        if kind == "media":
            try:
                yield _email_image_record(
                    part, where, builder, base=base, date=date, parent_locator=headers_locator
                )
            except (EvidenceImportError, ValidationError) as error:
                # Mislabelled or truncated attachments are common; keep the email text and
                # the attachments[] metadata entry rather than failing the whole .eml.
                # MediaStoreError is already wrapped as EvidenceImportError in _media_record;
                # build failures after a write unlink the file there before re-raising.
                detail = str(error)
                for prefix in (
                    f"{builder.source_file} {where}: ",
                    f"{builder.source_file} ",
                ):
                    if detail.startswith(prefix):
                        detail = detail[len(prefix) :]
                        break
                builder.sink.warnings.append(
                    ImportProblem(
                        builder.source_file,
                        f"{where}: attachment kept as metadata only ({detail})",
                    )
                )
            continue
        content = _part_text(part, name, where)
        if not content:
            continue
        details = {**base, "content_type": part.get_content_type()}
        if part.get_filename():
            details["filename"] = part.get_filename()
        charset = part.get_content_charset()
        if charset:
            details["charset"] = charset
        yield builder.build(
            locator=where,
            content=content,
            timestamp=date,
            timestamp_kind="sent",
            metadata=details,
        )


def _message_headers(
    message: EmailMessage, name: str, section: str | None
) -> tuple[dict[str, str], str | None, dict[str, Any]]:
    """The message's copied headers, its raw Date and the metadata shared by its records."""
    where = "" if section is None else f" part {section}"
    try:
        headers = {header: str(message[header]) for header in EML_HEADERS if message[header]}
    except Exception as error:
        raise EvidenceImportError(
            f"{name}{where} has a header that cannot be decoded: {error}"
        ) from error
    # The raw Date header, exactly as written: the parsed header is empty when the date is
    # not a valid RFC 5322 date, and a source timestamp must never be dropped.
    raw_date = next((value for key, value in message.raw_items() if key.lower() == "date"), None)
    date = " ".join(raw_date.split()) if raw_date and raw_date.strip() else None
    if date is not None:
        headers["Date"] = date
        headers = {header: headers[header] for header in EML_HEADERS if header in headers}
    base: dict[str, Any] = {
        "subject": headers.get("Subject"),
        "message_id": headers.get("Message-ID"),
    }
    base = {key: value for key, value in base.items() if value}
    if date is not None:
        try:
            base["date_iso"] = parsedate_to_datetime(date).isoformat()
        except (TypeError, ValueError, IndexError):
            base["date_unparsed"] = True
    return headers, date, base


def _collect_parts(
    part: EmailMessage,
    section: str | None,
    entries: list[tuple[str, str, EmailMessage]],
    attachments: list[dict[str, Any]],
    *,
    promote_images: bool,
    collect_text: bool = True,
) -> None:
    """Walk the MIME tree, numbering parts like IMAP sections (1, 1.2, ...). Text parts
    and forwarded messages go to ``entries`` in document order; other leaves are listed
    in ``attachments``. When ``promote_images`` is true, PNG/JPEG leaves also get a
    media entry so they become real records. ``collect_text`` is False when walking a
    non-preferred ``multipart/alternative`` sibling so inline images are still found
    without duplicating HTML/plain body text."""
    if part.get_content_type() == "message/rfc822":
        # A forwarded or attached message, expanded later like the file's own message.
        # (The email library reports it as multipart, so check it first.)
        if collect_text:
            entries.append(("message", section or "1", part))
        return
    if part.is_multipart():
        children = list(part.iter_parts())
        numbered = list(enumerate(children, 1))
        if part.get_content_subtype() == "alternative":
            # Prefer one text form; still walk non-preferred multiparts for inline
            # images (typical: text/plain + multipart/related with cid PNGs).
            preferred = _preferred_alternative(children)
            for index, child in numbered:
                child_section = f"{section}.{index}" if section else str(index)
                if child is preferred or (preferred is None and child.is_multipart()):
                    _collect_parts(
                        child,
                        child_section,
                        entries,
                        attachments,
                        promote_images=promote_images,
                        collect_text=collect_text,
                    )
                elif promote_images and child.is_multipart():
                    _collect_parts(
                        child,
                        child_section,
                        entries,
                        attachments,
                        promote_images=True,
                        collect_text=False,
                    )
            return
        for index, child in numbered:
            child_section = f"{section}.{index}" if section else str(index)
            _collect_parts(
                child,
                child_section,
                entries,
                attachments,
                promote_images=promote_images,
                collect_text=collect_text,
            )
        return

    locator = f"part {section}" if section else "body"
    content_type = part.get_content_type()
    if content_type in ("text/plain", "text/html"):
        if collect_text:
            entries.append(("text", locator, part))
        return
    # Prefer an exact decoded length for attachments[]. When a size hint already
    # exceeds the media cap, skip decode here (and again in _email_image_record)
    # so huge email images match the loose-file st_size gate.
    size_hint = _part_decoded_size_hint(part)
    if size_hint is not None and size_hint > MAX_MEDIA_BYTES:
        size_bytes = size_hint
    else:
        size_bytes = len(part.get_payload(decode=True) or b"")
    attachments.append(
        {
            "locator": locator,
            "filename": part.get_filename(),
            "content_type": content_type,
            "size_bytes": size_bytes,
        }
    )
    # PNG/JPEG attachments also become real media records (BLA-68) when a media_dir
    # is available; other binaries stay metadata-only in the attachments list above.
    # Oversized parts still get a media entry so _email_image_record can reject with
    # the same metadata-only warning path — but size is gated before decode there.
    if promote_images and content_type in EMAIL_IMAGE_TYPES:
        entries.append(("media", locator, part))


def _part_decoded_size_hint(part: EmailMessage) -> int | None:
    """Best-effort decoded payload size without decoding, or None if unknown.

    Prefers Content-Length; otherwise estimates from the undecoded body and
    Content-Transfer-Encoding (base64 ≈ 3/4 of non-whitespace length).
    """
    raw_length = part.get("Content-Length")
    if raw_length is not None:
        try:
            return int(str(raw_length).strip())
        except ValueError:
            pass
    payload = part.get_payload(decode=False)
    if payload is None:
        return 0
    if isinstance(payload, list | EmailMessage):
        return None
    if isinstance(payload, str):
        encoded_len = len(payload)
        non_ws = sum(1 for char in payload if not char.isspace())
    elif isinstance(payload, bytes | bytearray):
        encoded_len = len(payload)
        non_ws = sum(1 for byte in payload if byte not in b" \t\r\n")
    else:
        return None
    cte = (part.get("Content-Transfer-Encoding") or "").strip().lower()
    if cte == "base64":
        return (non_ws * 3) // 4
    if cte == "quoted-printable":
        # QP is roughly 1:1 aside from soft line breaks and =XX escapes; encoded
        # length is a safe upper bound for the gate.
        return encoded_len
    return encoded_len


def _email_image_record(
    part: EmailMessage,
    locator: str,
    builder: _RecordBuilder,
    *,
    base: Mapping[str, Any],
    date: str | None,
    parent_locator: str,
) -> EvidenceRecord:
    """Turn a PNG/JPEG MIME part into a media EvidenceRecord with parent email linkage."""
    content_type = part.get_content_type()
    filename = part.get_filename()
    extension = extension_for_content_type(content_type, filename)
    if extension is None:
        raise EvidenceImportError(
            f"{builder.source_file} {locator}: unsupported image type {content_type}"
        )
    # Gate on declared/estimated size before get_payload(decode=True), matching the
    # loose-file st_size check so huge attachments never force a full decode.
    size_hint = _part_decoded_size_hint(part)
    if size_hint is not None and size_hint > MAX_MEDIA_BYTES:
        raise EvidenceImportError(
            f"{builder.source_file} {locator}: attachment is {size_hint} bytes; "
            f"media limit is {MAX_MEDIA_BYTES} bytes"
        )
    data = part.get_payload(decode=True) or b""
    if len(data) > MAX_MEDIA_BYTES:
        raise EvidenceImportError(
            f"{builder.source_file} {locator}: attachment is {len(data)} bytes; "
            f"media limit is {MAX_MEDIA_BYTES} bytes"
        )
    details = {
        **base,
        "parent_locator": parent_locator,
        "content_type": content_type,
    }
    return _media_record(
        builder,
        locator=locator,
        data=data,
        extension=extension,
        content_type=content_type,
        modality="image",
        filename=filename,
        metadata=details,
        timestamp=date,
        timestamp_kind="sent",
    )


def _preferred_alternative(children: list[EmailMessage]) -> EmailMessage | None:
    for wanted in ("text/plain", "text/html"):
        for child in children:
            if child.get_content_type() == wanted:
                return child
    return None


def _decoded(part: EmailMessage, name: str, locator: str) -> Any:
    try:
        return part.get_content()
    except (LookupError, UnicodeError, ValueError, AssertionError) as error:
        raise EvidenceImportError(
            f"{name} {locator} could not be decoded ({part.get_content_type()}, charset "
            f"{part.get_content_charset() or 'unspecified'}): {error}; none of this file's "
            "records were imported"
        ) from error


def _part_text(part: EmailMessage, name: str, locator: str) -> str:
    text = _decoded(part, name, locator)
    if part.get_content_type() == "text/html":
        text = _html_to_text(text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()


class _HTMLText(HTMLParser):
    _BLOCKS = {
        "p",
        "div",
        "br",
        "tr",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "table",
        "hr",
    }
    _SKIP = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self.skipping += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self.skipping:
            self.skipping -= 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def _html_to_text(markup: str) -> str:
    """Visible text of an HTML part: tags, scripts and styles dropped, blocks on new lines."""
    parser = _HTMLText()
    parser.feed(markup)
    parser.close()
    # convert_charrefs=True has already decoded entities once; decoding again would turn
    # an escaped "&lt;b&gt;" in the source into a tag.
    text = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
