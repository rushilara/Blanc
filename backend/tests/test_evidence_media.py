"""Media import (BLA-68): email PNG/JPEG attachments, loose images, and PDFs.

Bytes land under the case media directory; SQLite/contract fields hold path, hash,
modality and content type—not the blob.
"""

from __future__ import annotations

import hashlib
from email.message import EmailMessage
from pathlib import Path

import pytest
from _paths import CONTRACTS_DIR, load_json
from jsonschema import Draft202012Validator
from sqlalchemy import text

from app.case_media import MediaStoreError, store_media_bytes
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import EmbeddingError, FakeEmbedder
from app.evidence import EvidenceImportError, UnsupportedFormatError, import_file, import_path
from app.evidence.importer import ImportSink
from app.evidence.records import stable_record_id
from app.evidence_store import get_record, store_records
from app.models import Case

RECORD_SCHEMA = Draft202012Validator(load_json(CONTRACTS_DIR / "evidence_record.schema.json"))

# Minimal valid PNG (1x1 transparent) and JPEG (1x1).
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG_BYTES = bytes(
    [
        0xFF,
        0xD8,
        0xFF,
        0xE0,
        0x00,
        0x10,
        0x4A,
        0x46,
        0x49,
        0x46,
        0x00,
        0x01,
        0x01,
        0x00,
        0x00,
        0x01,
        0x00,
        0x01,
        0x00,
        0x00,
        0xFF,
        0xD9,
    ]
)
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n2 0 obj\n<< /Type /Page >>\nendobj\n"


DATE = "Fri, 10 Apr 2026 09:15:00 -0400"


def assert_schema(record) -> None:
    assert not list(RECORD_SCHEMA.iter_errors(record.to_contract_json()))


def write_bytes(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_loose_png_becomes_a_media_record(tmp_path: Path) -> None:
    path = write_bytes(tmp_path, "photos/shot.png", PNG_BYTES)
    media_dir = tmp_path / "case-a" / "media"
    (record,) = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)

    assert record.source_file == "photos/shot.png"
    assert record.source_locator == "entire file"
    assert record.content == ""
    assert record.modality == "image"
    assert record.content_type == "image/png"
    assert record.media_path == f"media/{record.record_id}.png"
    assert record.metadata["filename"] == "shot.png"
    assert record.metadata["media_sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()
    assert record.original_sha256 == hashlib.sha256(PNG_BYTES).hexdigest()
    assert (media_dir / f"{record.record_id}.png").read_bytes() == PNG_BYTES
    assert_schema(record)


def test_loose_jpeg_and_pdf_import(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    jpg = write_bytes(tmp_path, "a.jpg", JPEG_BYTES)
    pdf = write_bytes(tmp_path, "report.pdf", PDF_BYTES)

    jpeg_rec = import_file(jpg, "case-a", source_root=tmp_path, media_dir=media_dir)[0]
    pdf_rec = import_file(pdf, "case-a", source_root=tmp_path, media_dir=media_dir)[0]

    assert jpeg_rec.modality == "image" and jpeg_rec.content_type == "image/jpeg"
    assert jpeg_rec.media_path.endswith(".jpg")
    assert pdf_rec.modality == "pdf" and pdf_rec.content_type == "application/pdf"
    assert pdf_rec.source_locator == "entire file"
    assert (media_dir / f"{pdf_rec.record_id}.pdf").read_bytes() == PDF_BYTES
    for record in (jpeg_rec, pdf_rec):
        assert_schema(record)


def test_email_png_attachment_becomes_its_own_record(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["From"] = "Alex <alex@example.test>"
    msg["To"] = "Jordan <jordan@example.test>"
    msg["Date"] = DATE
    msg["Subject"] = "Photo"
    msg["Message-ID"] = "<photo@example.test>"
    msg.set_content("See the attached photo.")
    msg.add_attachment(PNG_BYTES, maintype="image", subtype="png", filename="dock.png")

    path = write_bytes(tmp_path, "mail.eml", msg.as_bytes())
    media_dir = tmp_path / "media"
    records = {
        r.source_locator: r
        for r in import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    }

    # set_content + add_attachment -> multipart/mixed: text is part 1, image part 2.
    assert set(records) == {"headers", "part 1", "part 2"}
    assert records["part 1"].content == "See the attached photo."
    image = records["part 2"]
    assert image.modality == "image"
    assert image.content_type == "image/png"
    assert image.content == ""
    assert image.metadata["filename"] == "dock.png"
    assert image.metadata["parent_locator"] == "headers"
    assert image.metadata["subject"] == "Photo"
    assert image.timestamp == DATE
    assert (media_dir / f"{image.record_id}.png").read_bytes() == PNG_BYTES
    # Still listed on the headers record as attachment metadata.
    assert records["headers"].metadata["attachments"] == [
        {
            "locator": "part 2",
            "filename": "dock.png",
            "content_type": "image/png",
            "size_bytes": len(PNG_BYTES),
        }
    ]
    assert_schema(image)


def test_email_pdf_attachment_stays_metadata_only(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["From"] = "Alex <alex@example.test>"
    msg["To"] = "Jordan <jordan@example.test>"
    msg["Date"] = DATE
    msg["Subject"] = "Scan"
    msg["Message-ID"] = "<scan@example.test>"
    msg.set_content("See the scan.")
    msg.add_attachment(PDF_BYTES, maintype="application", subtype="pdf", filename="scan.pdf")

    path = write_bytes(tmp_path, "mail.eml", msg.as_bytes())
    media_dir = tmp_path / "media"
    records = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    assert [r.source_locator for r in records] == ["headers", "part 1"]
    assert records[0].metadata["attachments"][0]["filename"] == "scan.pdf"
    assert not media_dir.exists() or list(media_dir.iterdir()) == []


def test_mislabeled_email_image_falls_back_to_metadata(tmp_path: Path) -> None:
    """JPEG bytes labelled image/png must not fail the whole email."""
    msg = EmailMessage()
    msg["From"] = "Alex <alex@example.test>"
    msg["To"] = "Jordan <jordan@example.test>"
    msg["Date"] = DATE
    msg["Subject"] = "Bad label"
    msg["Message-ID"] = "<bad@example.test>"
    msg.set_content("Body text must survive.")
    msg.add_attachment(JPEG_BYTES, maintype="image", subtype="png", filename="lie.png")

    path = write_bytes(tmp_path, "mail.eml", msg.as_bytes())
    media_dir = tmp_path / "media"
    sink = ImportSink()
    records = import_file(
        path, "case-a", source_root=tmp_path, media_dir=media_dir, sink=sink
    )
    assert [r.source_locator for r in records] == ["headers", "part 1"]
    assert records[1].content == "Body text must survive."
    assert records[0].metadata["attachments"][0]["filename"] == "lie.png"
    assert sink.warnings
    assert "metadata only" in sink.warnings[0].reason
    assert not media_dir.exists() or list(media_dir.iterdir()) == []


def test_reimport_same_bytes_is_idempotent(tmp_path: Path) -> None:
    path = write_bytes(tmp_path, "shot.png", PNG_BYTES)
    media_dir = tmp_path / "media"
    first = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)[0]
    second = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)[0]
    assert first.record_id == second.record_id
    assert first.media_path == second.media_path
    assert first.metadata["media_sha256"] == second.metadata["media_sha256"]
    assert (media_dir / f"{first.record_id}.png").read_bytes() == PNG_BYTES


def test_changed_media_bytes_are_detected(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    record_id = stable_record_id("case-a", "shot.png", "entire file")
    store_media_bytes(media_dir, record_id, PNG_BYTES, ".png")
    # Same record_id path, different bytes.
    with pytest.raises(MediaStoreError, match="different bytes"):
        store_media_bytes(media_dir, record_id, JPEG_BYTES, ".png")

    path = write_bytes(tmp_path, "shot.png", JPEG_BYTES)
    # JPEG magic with a .png name fails verification before the store conflict.
    with pytest.raises(EvidenceImportError, match="not a valid PNG"):
        import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)


def test_store_media_refuses_symlink_destination(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    record_id = stable_record_id("case-a", "shot.png", "entire file")
    dest = media_dir / f"{record_id}.png"
    dest.symlink_to(outside)
    with pytest.raises(MediaStoreError, match="symbolic link"):
        store_media_bytes(media_dir, record_id, PNG_BYTES, ".png")
    assert outside.read_bytes() == b"secret"


def test_store_media_uses_replace_not_partial_dest(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    record_id = stable_record_id("case-a", "shot.png", "entire file")
    path, digest, created = store_media_bytes(media_dir, record_id, PNG_BYTES, ".png")
    assert created is True
    assert path == f"media/{record_id}.png"
    assert digest == hashlib.sha256(PNG_BYTES).hexdigest()
    again = store_media_bytes(media_dir, record_id, PNG_BYTES, ".png")
    assert again[2] is False


def test_parse_only_folder_skips_media_without_media_dir(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("a note\n", encoding="utf-8")
    write_bytes(tmp_path, "shot.png", PNG_BYTES)
    write_bytes(tmp_path, "report.pdf", PDF_BYTES)

    result = import_path(tmp_path, "case-a")
    assert [r.source_file for r in result.records] == ["notes.txt"]
    skipped = {item.source_file: item.reason for item in result.skipped}
    assert "shot.png" in skipped and "unsupported format" in skipped["shot.png"]
    assert "report.pdf" in skipped


def test_parse_only_single_media_needs_media_dir(tmp_path: Path) -> None:
    path = write_bytes(tmp_path, "shot.png", PNG_BYTES)
    with pytest.raises(UnsupportedFormatError, match="without media_dir"):
        import_file(path, "case-a", source_root=tmp_path)


def test_folder_import_mixes_text_and_media(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("a note\n", encoding="utf-8")
    write_bytes(tmp_path, "shot.png", PNG_BYTES)
    write_bytes(tmp_path, "report.pdf", PDF_BYTES)
    media_dir = tmp_path / "out-media"

    result = import_path(tmp_path, "case-a", media_dir=media_dir)
    by_file = {r.source_file: r for r in result.records}
    assert set(by_file) == {"notes.txt", "shot.png", "report.pdf"}
    assert by_file["notes.txt"].content == "a note"
    assert by_file["shot.png"].modality == "image"
    assert by_file["report.pdf"].modality == "pdf"
    assert result.errors == []
    assert set(result.written_media) == {
        by_file["shot.png"].record_id,
        by_file["report.pdf"].record_id,
    }


def test_media_round_trip_through_store_records(tmp_path: Path) -> None:
    """Storing and reading back must keep media_path / modality / content_type."""
    path = write_bytes(tmp_path, "shot.png", PNG_BYTES)
    media_dir = tmp_path / "media"
    (record,) = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)

    engine = make_engine(f"sqlite:///{tmp_path / 'round.db'}")
    init_db(engine, FakeEmbedder().dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="Media"))
        session.flush()
        stored = store_records(session, "case-a", [record], FakeEmbedder())
        session.commit()
        assert stored.inserted == 1
        loaded = get_record(session, "case-a", record.record_id)
        assert loaded is not None
        assert loaded.modality == "image"
        assert loaded.media_path == record.media_path
        assert loaded.content_type == "image/png"
        assert loaded.content == ""
        assert loaded.to_contract_json()["media_path"] == record.media_path
    engine.dispose()


def test_init_db_adds_media_columns_on_existing_table(tmp_path: Path) -> None:
    """ALTER TABLE migration must add modality/media_path/content_type on old DBs."""
    db_path = tmp_path / "old.db"
    engine = make_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE evidence_records ("
                "case_id VARCHAR(64) NOT NULL, "
                "record_id VARCHAR(64) NOT NULL, "
                "PRIMARY KEY (case_id, record_id))"
            )
        )
        cols = {row[1] for row in connection.execute(text("PRAGMA table_info(evidence_records)"))}
        assert "modality" not in cols
    init_db(engine, FakeEmbedder().dim)
    with engine.connect() as connection:
        cols = {row[1] for row in connection.execute(text("PRAGMA table_info(evidence_records)"))}
    assert {"modality", "media_path", "content_type"} <= cols
    engine.dispose()


def test_schema_rejects_traversal_media_path_and_odd_content_type() -> None:
    good = {
        "record_id": "rec_0123456789abcdef",
        "case_id": "case-a",
        "source_file": "shot.png",
        "source_locator": "entire file",
        "content": "",
        "modality": "image",
        "media_path": "media/rec_0123456789abcdef.png",
        "content_type": "image/png",
    }
    assert list(RECORD_SCHEMA.iter_errors(good)) == []
    bad_path = {**good, "media_path": "../../../etc/passwd"}
    assert list(RECORD_SCHEMA.iter_errors(bad_path))
    bad_type = {**good, "content_type": "text/html"}
    assert list(RECORD_SCHEMA.iter_errors(bad_type))
    missing = {
        "record_id": "rec_0123456789abcdef",
        "case_id": "case-a",
        "source_file": "shot.png",
        "source_locator": "entire file",
        "content": "",
        "modality": "image",
    }
    assert list(RECORD_SCHEMA.iter_errors(missing))


def test_cli_cleans_up_media_when_embedding_fails(tmp_path: Path) -> None:
    from app.evidence.__main__ import main

    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "notes.txt").write_text("hello\n", encoding="utf-8")
    write_bytes(exports, "shot.png", PNG_BYTES)
    db = tmp_path / "blanc.db"
    media_dir = tmp_path / "media"

    class BoomEmbedder(FakeEmbedder):
        def embed_documents(self, texts):  # noqa: ANN001
            raise EmbeddingError("boom")

    code = main(
        [
            str(exports),
            "--store",
            "--title",
            "Orphan check",
            "--database-url",
            f"sqlite:///{db}",
            "--media-dir",
            str(media_dir),
        ],
        embedder=BoomEmbedder(),
    )
    assert code == 1
    leftover = list(media_dir.rglob("*")) if media_dir.exists() else []
    assert leftover == []
