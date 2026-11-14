"""Corpus integrity check: catch bad imports before they are indexed (BLA-11).

Run from `backend/` against the JSON written by the importer:

    python -m app.evidence.integrity records.json --case-id my-case --source-root path/to/exports

Exits 0 when the corpus is clean, 1 when it finds problems and 2 when it cannot
run at all (unreadable input, bad arguments).

Checks:
- every record is a valid `EvidenceRecord`;
- no `record_id` appears twice;
- every record belongs to the expected `case_id`;
- every `source_file` exists under the source root, and still has the SHA-256
  recorded at import;
- no record comes from a file named like an answer key or case brief.
"""

import argparse
import fnmatch
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import ValidationError

from app.evidence.importer import DEFAULT_SKIP_PATTERNS
from app.evidence.records import EvidenceRecord

IssueKind = Literal[
    "invalid_record",
    "duplicate_record_id",
    "case_mismatch",
    "missing_source_file",
    "source_changed",
    "restricted_source",
]


@dataclass(frozen=True)
class IntegrityIssue:
    kind: IssueKind
    detail: str
    record_id: str | None = None
    source_file: str | None = None

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


def check_corpus(
    records: Iterable[Any],
    *,
    case_id: str,
    source_root: str | Path | None = None,
    restricted_patterns: Iterable[str] = DEFAULT_SKIP_PATTERNS,
) -> list[IntegrityIssue]:
    """Return every problem found. An empty list means the corpus is clean.

    `source_root` is the folder the records were imported from. Pass `None` to
    skip the source-file checks, for example when the original exports are not
    on this machine; the caller should say so rather than report a clean result.
    """
    issues: list[IntegrityIssue] = []
    first_seen: dict[str, int] = {}
    repeats: dict[str, int] = {}
    by_source: dict[str, list[tuple[str | None, str | None]]] = {}

    for index, raw in enumerate(records):
        where = f"record at index {index}"
        if not isinstance(raw, Mapping):
            issues.append(IntegrityIssue("invalid_record", f"{where} is not an object"))
            continue

        record_id = raw.get("record_id") if isinstance(raw.get("record_id"), str) else None
        source_file = raw.get("source_file") if isinstance(raw.get("source_file"), str) else None
        label = record_id or where

        try:
            EvidenceRecord(**raw)
        except (ValidationError, TypeError) as error:
            issues.append(
                IntegrityIssue("invalid_record", f"{label}: {_summarize(error)}", record_id)
            )

        if record_id:
            if record_id in first_seen:
                repeats[record_id] = repeats.get(record_id, 1) + 1
            else:
                first_seen[record_id] = index

        record_case = raw.get("case_id")
        if record_case != case_id:
            issues.append(
                IntegrityIssue(
                    "case_mismatch",
                    f"{label} belongs to case {record_case!r}, expected {case_id!r}",
                    record_id,
                    source_file,
                )
            )

        if source_file:
            sha256 = raw.get("original_sha256")
            by_source.setdefault(source_file, []).append(
                (record_id, sha256 if isinstance(sha256, str) else None)
            )

    for record_id, count in repeats.items():
        issues.append(
            IntegrityIssue(
                "duplicate_record_id", f"{record_id} appears {count} times", record_id
            )
        )

    patterns = tuple(pattern.lower() for pattern in restricted_patterns)
    for source_file, entries in by_source.items():
        name = PurePosixPath(source_file).name.lower()
        if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
            issues.append(
                IntegrityIssue(
                    "restricted_source",
                    f"{source_file} looks like an answer key or case brief and must not be "
                    f"in the corpus ({_count(entries)})",
                    entries[0][0],
                    source_file,
                )
            )

    if source_root is not None:
        root = Path(source_root).resolve()
        for source_file, entries in by_source.items():
            issues.extend(_check_source(root, source_file, entries))

    return issues


def load_records(path: str | Path) -> list[Any]:
    """Read the importer's JSON output. Raises `ValueError` if it is not a JSON array."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Could not read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error.msg}") from error
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array of evidence records")
    return data


def _check_source(
    root: Path, source_file: str, entries: list[tuple[str | None, str | None]]
) -> list[IntegrityIssue]:
    first_id = entries[0][0]
    candidate = (root / source_file).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return [
            IntegrityIssue(
                "missing_source_file",
                f"{source_file} not found under the source root ({_count(entries)})",
                first_id,
                source_file,
            )
        ]
    expected = {sha256 for _, sha256 in entries if sha256}
    if not expected:
        return []
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if expected == {actual}:
        return []
    return [
        IntegrityIssue(
            "source_changed",
            f"{source_file} no longer matches the SHA-256 recorded at import "
            f"({_count(entries)})",
            first_id,
            source_file,
        )
    ]


def _count(entries: list[tuple[str | None, str | None]]) -> str:
    return "1 record" if len(entries) == 1 else f"{len(entries)} records"


def _summarize(error: ValidationError | TypeError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'record'}: {issue['msg']}"
            for issue in error.errors()
        )
    return str(error)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evidence.integrity",
        description="Check an imported corpus for duplicate IDs, missing sources and "
        "case mismatches.",
    )
    parser.add_argument("records", type=Path, help="JSON array written by the importer.")
    parser.add_argument("--case-id", required=True, help="Case every record must belong to.")
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--source-root", type=Path, help="Folder the records were imported from.")
    sources.add_argument(
        "--skip-source-check",
        action="store_true",
        help="Do not check that source files exist (use only when they are unavailable).",
    )
    args = parser.parse_args(argv)

    try:
        records = load_records(args.records)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.source_root is not None and not args.source_root.is_dir():
        print(f"error: source root is not a folder: {args.source_root}", file=sys.stderr)
        return 2

    issues = check_corpus(records, case_id=args.case_id, source_root=args.source_root)
    for issue in issues:
        print(issue)
    if args.skip_source_check:
        print("note: source files were not checked (--skip-source-check)")
    if issues:
        print(f"FAILED: {len(issues)} problems in {len(records)} records")
        return 1
    print(f"OK: {len(records)} records, no problems found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
