"""Import extracted evidence files (TXT, JSON, CSV) as `EvidenceRecord` objects.

Case-agnostic by design: nothing here knows about any particular case. One
source file can yield many records (one per message, row or paragraph), and
every record keeps the file it came from and a precise locator inside it.

Locator conventions:
- TXT: `line 12`, `lines 12-15` or `entire file` (line numbers are 1-based).
- JSON: `[3]` for a top-level array, `messages[3]` for an array under a key,
  `$` for a single top-level object (indexes are 0-based, as in JSON).
- CSV: `line 4`, or `lines 4-6` for a row whose quoted cells span several lines.
  These are physical line numbers in the file, so the header is line 1 and
  blank lines are counted.

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
import fnmatch
import hashlib
import io
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import ValidationError

from app.evidence.records import EvidenceRecord, TimestampKind, stable_record_id

SUPPORTED_SUFFIXES = (".txt", ".json", ".csv")

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
class ImportResult:
    records: list[EvidenceRecord] = field(default_factory=list)
    skipped: list[ImportProblem] = field(default_factory=list)
    errors: list[ImportProblem] = field(default_factory=list)


def import_path(
    path: str | Path,
    case_id: str,
    *,
    source_root: str | Path | None = None,
    txt_mode: TxtMode = "paragraph",
    skip_patterns: Iterable[str] = DEFAULT_SKIP_PATTERNS,
    strict: bool = False,
) -> ImportResult:
    """Import one file or every supported file under a folder.

    `source_root` is the top folder of the case export. It defaults to `path`
    when `path` is a folder and is required when `path` is a single file, so
    record IDs never depend on how the import was started.

    Files are visited in sorted order so results are deterministic. A file that
    fails is reported in `errors` and never half-imported; with `strict=True`
    the first failure raises instead. Listed in `skipped`, not `errors`: symbolic
    links, hidden files and folders, anything under a name matching
    `skip_patterns`, and, when importing a folder, files in a format the importer
    does not handle.
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
        try:
            records = import_file(file_path, case_id, source_file=source_file, txt_mode=txt_mode)
            clash = next((record for record in records if record.record_id in seen_ids), None)
            if clash is not None:
                raise EvidenceImportError(
                    f"{source_file} repeats record_id {clash.record_id}, which another file "
                    "already provided; none of this file's records were imported"
                )
        except EvidenceImportError as error:
            if is_folder and isinstance(error, UnsupportedFormatError):
                shown = file_path.suffix.lower() or "(no extension)"
                result.skipped.append(ImportProblem(source_file, f"unsupported format {shown}"))
                continue
            if strict:
                raise
            result.errors.append(ImportProblem(source_file, str(error)))
            continue
        seen_ids.update(record.record_id for record in records)
        result.records.extend(records)

    return result


def import_file(
    path: str | Path,
    case_id: str,
    *,
    source_root: str | Path | None = None,
    source_file: str | None = None,
    txt_mode: TxtMode = "paragraph",
) -> list[EvidenceRecord]:
    """Import a single file. Raises `EvidenceImportError` with a clear reason on failure.

    Give either `source_root` (the top folder of the case export, from which the
    file's relative path is taken) or `source_file` (that relative path itself).
    One of them is required: record IDs are derived from the path, so it must not
    depend on which folder the import happened to start from.
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
    try:
        raw = file_path.read_bytes()
    except OSError as error:
        raise EvidenceImportError(f"Could not read {source}: {error}") from error

    sha256 = hashlib.sha256(raw).hexdigest()
    text, encoding = _decode(raw)
    builder = _RecordBuilder(case_id=case_id, source_file=source, sha256=sha256, encoding=encoding)

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

    def build(
        self,
        *,
        locator: str,
        content: str,
        timestamp: str | None = None,
        timestamp_kind: TimestampKind = "unknown",
        metadata: Mapping[str, Any] | None = None,
    ) -> EvidenceRecord:
        details = dict(metadata or {})
        if self.encoding != "utf-8":
            details.setdefault("source_encoding", self.encoding)
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
        )


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
                f"line {start_line}"
                if start_line == end_line
                else f"lines {start_line}-{end_line}"
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
