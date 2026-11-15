"""Command line: import a file or folder of extracted evidence.

Run from `backend/` (or use `make import` from the repo root):

    # Parse only: write EvidenceRecord JSON.
    python -m app.evidence path/to/exports --case-id my-case --out records.json
    python -m app.evidence path/to/exports/chats/chat.json --source-root path/to/exports \\
        --case-id my-case --out records.json

    # Store into the local case database (DATABASE_URL), embedding chunks with Gemini.
    python -m app.evidence path/to/exports --store --title "Owl case"
    python -m app.evidence path/to/exports --store --case-id case_01...

`--store` writes records, chunks, vectors and the keyword index into the same local
SQLite file the backend uses, and the case becomes `ready` once it holds records. With
`--title`, a new case is created in the same transaction and its case_id is printed on
stdout; if the import or embedding fails, nothing is written, not even the case.

Text source files are read where they are and are not copied: each record keeps the
file's path relative to the source root and the file's SHA-256. Image and PDF bytes
are copied under ``{CASES_DIR}/{case_id}/media/`` (see BLA-68). Media records have
empty ``content`` and no chunks until multimodal embedding (BLA-69), so they are not
searchable yet. Re-running is safe: records already stored are skipped and nothing is
embedded twice; unchanged media files are not rewritten. If parsing or embedding
fails, the DB transaction is rolled back and any media files this run newly wrote are
deleted.

Exits nonzero if any file failed to import or any record was rejected, so a bad import
is never silent.
"""

import argparse
import json
import sys
from pathlib import Path

from app.case_media import remove_media_files
from app.embeddings import Embedder
from app.evidence.importer import EvidenceImportError, ImportResult, import_path


def _media_dir_for(args: argparse.Namespace, case_id: str) -> Path | None:
    """Directory for case media bytes, or None when the caller did not ask for one."""
    if args.media_dir is not None:
        return args.media_dir
    if args.store:
        from app.case_media import case_media_dir
        from app.config import get_settings

        return case_media_dir(get_settings().CASES_DIR, case_id)
    return None


def _import(args: argparse.Namespace, case_id: str) -> ImportResult | None:
    """Parse the evidence at ``args.path`` for ``case_id`` and report what happened."""
    try:
        result = import_path(
            args.path,
            case_id,
            source_root=args.source_root,
            media_dir=_media_dir_for(args, case_id),
            txt_mode=args.txt_mode,
            strict=args.strict,
        )
    except EvidenceImportError as error:
        print(f"error: {error}", file=sys.stderr)
        return None
    files = {record.source_file for record in result.records}
    print(f"Imported {len(result.records)} records from {len(files)} files.", file=sys.stderr)
    for item in result.skipped:
        print(f"skipped: {item.source_file} ({item.reason})", file=sys.stderr)
    for item in result.warnings:
        print(f"warning: {item.source_file}: {item.reason}", file=sys.stderr)
    for item in result.errors:
        print(f"error: {item.source_file}: {item.reason}", file=sys.stderr)
    return result


def _write_out(args: argparse.Namespace, result: ImportResult) -> None:
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload = [record.to_contract_json() for record in result.records]
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _import_and_store(args: argparse.Namespace, embedder: Embedder | None) -> int:
    """Import into the local case DB in one transaction: create the case (``--title``),
    parse, embed and store, then commit. Any failure rolls everything back and removes
    media files this run newly wrote."""
    from app.config import get_settings
    from app.db import init_db, make_engine, make_session_factory
    from app.embeddings import EmbeddingError, make_embedder
    from app.evidence_store import store_records
    from app.models import Case, EvidenceRecordRow

    settings = get_settings()
    url = args.database_url or settings.DATABASE_URL
    engine = make_engine(url)
    written_media: dict[str, Path] = {}
    committed = False
    result: ImportResult | None = None
    stored = None
    case_id = ""
    total = 0
    status = ""
    try:
        init_db(engine, settings.EMBEDDING_DIM)
        with make_session_factory(engine)() as session:
            if args.case_id:
                case_id = args.case_id
                if session.get(Case, case_id) is None:
                    print(
                        f"error: case {case_id!r} does not exist in {url}. Pass --title to "
                        "create a new case, or create one with POST /cases.",
                        file=sys.stderr,
                    )
                    return 1
            else:
                case = Case(title=args.title)
                session.add(case)
                session.flush()
                case_id = case.case_id

            result = _import(args, case_id)
            if result is None:
                return 1
            written_media = result.written_media
            if not result.records:
                print("error: no records to store; nothing was written.", file=sys.stderr)
                return 1
            try:
                stored = store_records(
                    session, case_id, result.records, embedder or make_embedder(settings)
                )
            except EmbeddingError as error:
                print(f"error: embedding failed; nothing was written: {error}", file=sys.stderr)
                return 1
            # Rejected records never landed in SQLite; drop any media this run created for them.
            remove_media_files(
                {
                    record_id: path
                    for record_id, path in written_media.items()
                    if any(rejected.record_id == record_id for rejected in stored.rejected)
                }
            )
            session.commit()
            committed = True
            total = session.query(EvidenceRecordRow).filter_by(case_id=case_id).count()
            status = session.get(Case, case_id).import_status
    finally:
        if not committed:
            remove_media_files(written_media)
        engine.dispose()

    if not committed or result is None or stored is None:
        return 1

    print(
        f"Stored in {url}: {stored.inserted} new, {stored.unchanged} already stored, "
        f"{stored.chunks_created} chunks embedded. Case {case_id} holds {total} records "
        f"({status}).",
        file=sys.stderr,
    )
    for record_id in stored.source_changed:
        print(f"source changed (kept stored version): {record_id}", file=sys.stderr)
    for rejected in stored.rejected:
        print(f"rejected: {rejected.record_id}: {rejected.reason}", file=sys.stderr)
    print(case_id)
    _write_out(args, result)
    return 1 if result.errors or stored.rejected else 0


def main(argv: list[str] | None = None, *, embedder: Embedder | None = None) -> int:
    """Run the CLI. ``embedder`` replaces the Gemini embedder (tests pass a fake)."""
    parser = argparse.ArgumentParser(
        prog="python -m app.evidence",
        description="Import TXT, JSON, CSV, EML, PNG/JPEG and PDF evidence files as "
        "EvidenceRecord objects, and optionally store them in the local case database.",
    )
    parser.add_argument("path", type=Path, help="A file or a folder of extracted evidence.")
    parser.add_argument("--case-id", help="Case these records belong to.")
    parser.add_argument("--out", type=Path, help="Write records here as a JSON array.")
    parser.add_argument(
        "--store",
        action="store_true",
        help="Store the records in the local case database (DATABASE_URL), embedding "
        "chunks with Gemini (needs GEMINI_API_KEY).",
    )
    parser.add_argument(
        "--title",
        help="With --store and no --case-id: create a new case with this title. Its "
        "case_id is printed on stdout.",
    )
    parser.add_argument(
        "--database-url",
        help="With --store: write to this database instead of DATABASE_URL from .env.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        help="Top folder of the case export. Defaults to PATH when PATH is a folder; "
        "required when PATH is a single file, so record IDs stay stable.",
    )
    parser.add_argument(
        "--media-dir",
        type=Path,
        help="Directory for image/PDF bytes (written as {record_id}.ext). With --store, "
        "defaults to {CASES_DIR}/{case_id}/media. Without --store, omit this to skip "
        "loose media files (folder import) the way unsupported formats are skipped.",
    )
    parser.add_argument(
        "--txt-mode",
        choices=("paragraph", "line", "file"),
        default="paragraph",
        help="How to split TXT files into records (default: paragraph).",
    )
    parser.add_argument("--strict", action="store_true", help="Stop at the first failing file.")
    args = parser.parse_args(argv)

    if args.case_id and args.title:
        parser.error("pass either --case-id or --title, not both")
    if (args.title or args.database_url) and not args.store:
        parser.error("--title and --database-url only apply with --store")
    if args.title is not None and not args.title.strip():
        parser.error("--title must not be blank")
    if not args.case_id and not args.title:
        parser.error("--case-id is required (or --store --title to create a new case)")

    if args.store:
        return _import_and_store(args, embedder)

    result = _import(args, args.case_id)
    if result is None:
        return 1
    _write_out(args, result)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
