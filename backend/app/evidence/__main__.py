"""Command line: import a file or folder of extracted evidence into EvidenceRecord JSON.

Run from `backend/`:

    python -m app.evidence path/to/exports --case-id my-case --out records.json
    python -m app.evidence path/to/exports/chats/chat.json --source-root path/to/exports \\
        --case-id my-case --out records.json

Exits nonzero if any file failed to import, so a bad import is never silent.
"""

import argparse
import json
import sys
from pathlib import Path

from app.evidence.importer import EvidenceImportError, import_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evidence",
        description="Import TXT, JSON and CSV evidence files as EvidenceRecord objects.",
    )
    parser.add_argument("path", type=Path, help="A file or a folder of extracted evidence.")
    parser.add_argument("--case-id", required=True, help="Case these records belong to.")
    parser.add_argument("--out", type=Path, help="Write records here as a JSON array.")
    parser.add_argument(
        "--source-root",
        type=Path,
        help="Top folder of the case export. Defaults to PATH when PATH is a folder; "
        "required when PATH is a single file, so record IDs stay stable.",
    )
    parser.add_argument(
        "--txt-mode",
        choices=("paragraph", "line", "file"),
        default="paragraph",
        help="How to split TXT files into records (default: paragraph).",
    )
    parser.add_argument("--strict", action="store_true", help="Stop at the first failing file.")
    args = parser.parse_args(argv)

    try:
        result = import_path(
            args.path,
            args.case_id,
            source_root=args.source_root,
            txt_mode=args.txt_mode,
            strict=args.strict,
        )
    except EvidenceImportError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload = [record.to_contract_json() for record in result.records]
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    files = {record.source_file for record in result.records}
    print(f"Imported {len(result.records)} records from {len(files)} files.", file=sys.stderr)
    for item in result.skipped:
        print(f"skipped: {item.source_file} ({item.reason})", file=sys.stderr)
    for item in result.errors:
        print(f"error: {item.source_file}: {item.reason}", file=sys.stderr)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
