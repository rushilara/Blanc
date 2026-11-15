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
from pydantic import ValidationError
from sqlalchemy import text

from app.case_media import (
    MediaStoreError,
    extension_for_content_type,
    store_media_bytes,
)
from app.contracts import EvidenceRecord
from app.db import init_db, make_engine, make_session_factory
from app.embeddings import EmbeddingError, FakeEmbedder, MediaInput
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


def test_email_alternative_related_inline_png_becomes_media(tmp_path: Path) -> None:
    """text/plain + multipart/related(HTML+cid PNG): prefer plain text, still promote PNG."""
    import base64

    html = (
        b"<html><body><p>See the photo.</p>"
        b'<img src="cid:dock@example.test" alt="dock"/></body></html>'
    )
    png_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
    raw = (
        b"From: Alex <alex@example.test>\r\n"
        b"To: Jordan <jordan@example.test>\r\n"
        b"Date: Fri, 10 Apr 2026 09:15:00 -0400\r\n"
        b"Subject: Inline photo\r\n"
        b"Message-ID: <inline@example.test>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/alternative; boundary="alt"\r\n'
        b"\r\n"
        b"--alt\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: 7bit\r\n"
        b"\r\n"
        b"See the photo.\r\n"
        b"--alt\r\n"
        b'Content-Type: multipart/related; boundary="rel"\r\n'
        b"\r\n"
        b"--rel\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: 7bit\r\n"
        b"\r\n" + html + b"\r\n"
        b"--rel\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"Content-ID: <dock@example.test>\r\n"
        b'Content-Disposition: inline; filename="dock.png"\r\n'
        b"\r\n" + png_b64.encode("ascii") + b"\r\n"
        b"--rel--\r\n"
        b"--alt--\r\n"
    )
    path = write_bytes(tmp_path, "inline.eml", raw)
    media_dir = tmp_path / "media"
    records = {
        r.source_locator: r
        for r in import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    }

    # Prefer plain text (part 1); do not import the HTML twin; promote the cid PNG.
    assert "part 1" in records
    assert records["part 1"].content == "See the photo."
    assert "part 2.1" not in records  # HTML under related, not preferred
    image = records["part 2.2"]
    assert image.modality == "image"
    assert image.content_type == "image/png"
    assert image.metadata["filename"] == "dock.png"
    assert (media_dir / f"{image.record_id}.png").read_bytes() == PNG_BYTES
    assert any(
        a["locator"] == "part 2.2" and a["content_type"] == "image/png"
        for a in records["headers"].metadata["attachments"]
    )
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
    records = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir, sink=sink)
    assert [r.source_locator for r in records] == ["headers", "part 1"]
    assert records[1].content == "Body text must survive."
    assert records[0].metadata["attachments"][0]["filename"] == "lie.png"
    assert sink.warnings
    assert sink.warnings[0].source_file == "mail.eml"
    assert sink.warnings[0].reason.startswith("part ")
    assert sink.warnings[0].reason.count("part ") == 1  # prefix not duplicated
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
    """Storing embeds media bytes and indexes an FTS sidecar (filename)."""
    from sqlalchemy import text

    from app.evidence_store import list_chunks
    from app.search_index import FTS_TABLE, VEC_TABLE, keyword_search, to_fts_query

    path = write_bytes(tmp_path, "shot.png", PNG_BYTES)
    media_dir = tmp_path / "media"
    (record,) = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)

    embedder = FakeEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'round.db'}")
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="Media"))
        session.flush()
        stored = store_records(session, "case-a", [record], embedder, media_dir=media_dir)
        session.commit()
        assert stored.inserted == 1
        assert stored.chunks_created == 1
        loaded = get_record(session, "case-a", record.record_id)
        assert loaded is not None
        assert loaded.modality == "image"
        assert loaded.media_path == record.media_path
        assert loaded.content_type == "image/png"
        assert loaded.content == ""
        assert loaded.to_contract_json()["media_path"] == record.media_path
        chunks = list_chunks(session, "case-a", record.record_id)
        assert len(chunks) == 1
        assert chunks[0].text == "shot.png"
        assert embedder.embedded_media and embedder.embedded_media[0].data == PNG_BYTES
        assert session.execute(text(f"SELECT count(*) FROM {VEC_TABLE}")).scalar() == 1
        assert session.execute(text(f"SELECT count(*) FROM {FTS_TABLE}")).scalar() == 1
        hits = keyword_search(session, "case-a", to_fts_query("shot"))
        assert [h.record_id for h in hits] == [record.record_id]
    engine.dispose()


def test_email_image_fts_sidecar_includes_subject(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["From"] = "Alex <alex@example.test>"
    msg["To"] = "Jordan <jordan@example.test>"
    msg["Date"] = DATE
    msg["Subject"] = "Warehouse photo"
    msg["Message-ID"] = "<photo2@example.test>"
    msg.set_content("See attached.")
    msg.add_attachment(PNG_BYTES, maintype="image", subtype="png", filename="dock.png")

    path = write_bytes(tmp_path, "mail.eml", msg.as_bytes())
    media_dir = tmp_path / "media"
    records = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    image = next(r for r in records if r.modality == "image")

    embedder = FakeEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'email.db'}")
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="Email media"))
        session.flush()
        store_records(session, "case-a", records, embedder, media_dir=media_dir)
        session.commit()
        from app.evidence_store import list_chunks
        from app.search_index import keyword_search, to_fts_query

        chunks = list_chunks(session, "case-a", image.record_id)
        assert len(chunks) == 1
        assert "dock.png" in chunks[0].text
        assert "Warehouse photo" in chunks[0].text
        assert "From: Alex" in chunks[0].text
        hits = keyword_search(session, "case-a", to_fts_query("Warehouse"))
        assert image.record_id in {h.record_id for h in hits}
    engine.dispose()


def test_pdf_truncated_at_embed_time(tmp_path: Path) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from pypdf import PdfReader, PdfWriter

    from app.case_media import GEMINI_PDF_MAX_PAGES, truncate_pdf_for_embedding
    from app.embeddings import GeminiEmbedder, MediaInput

    del tmp_path  # unused; keeps pytest fixture signature consistent with siblings
    writer = PdfWriter()
    for _ in range(GEMINI_PDF_MAX_PAGES + 3):
        writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    long_pdf = buf.getvalue()

    truncated = truncate_pdf_for_embedding(long_pdf)
    assert truncated.truncated is True
    assert truncated.embedding_pages == GEMINI_PDF_MAX_PAGES
    assert truncated.page_count == GEMINI_PDF_MAX_PAGES + 3
    assert len(PdfReader(BytesIO(truncated.data)).pages) == GEMINI_PDF_MAX_PAGES
    short = truncate_pdf_for_embedding(PDF_BYTES)
    assert short.data == PDF_BYTES and short.truncated is False

    models_requests: list = []

    class RecordingModels:
        def embed_content(self, *, model, contents, config):  # noqa: ANN001
            models_requests.append(contents)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * 8)])

    embedder = GeminiEmbedder(
        None, "gemini-embedding-2", 8, client=SimpleNamespace(models=RecordingModels())
    )
    embedder.embed_media([MediaInput(data=long_pdf, mime_type="application/pdf")])
    sent = models_requests[0][0].inline_data.data
    assert len(PdfReader(BytesIO(sent)).pages) == GEMINI_PDF_MAX_PAGES


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


def test_init_db_adds_embedding_coverage_on_existing_chunks(tmp_path: Path) -> None:
    """ALTER TABLE migration must add embedding_coverage so old DBs can still load chunks."""
    from sqlalchemy import select

    from app.models import EvidenceChunk

    db_path = tmp_path / "old_chunks.db"
    engine = make_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE cases ("
                "case_id VARCHAR(64) PRIMARY KEY, "
                "title VARCHAR(200) NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "import_status VARCHAR(16) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE evidence_records ("
                "case_id VARCHAR(64) NOT NULL, "
                "record_id VARCHAR(200) NOT NULL, "
                "source_file TEXT NOT NULL, "
                "source_locator TEXT NOT NULL, "
                "content TEXT NOT NULL, "
                "content_hash VARCHAR(64) NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "PRIMARY KEY (case_id, record_id))"
            )
        )
        # Pre-BLA-69 shape: no embedding_coverage (create_all will not add it later).
        connection.execute(
            text(
                "CREATE TABLE evidence_chunks ("
                "chunk_id VARCHAR(64) PRIMARY KEY, "
                "case_id VARCHAR(64) NOT NULL, "
                "record_id VARCHAR(200) NOT NULL, "
                "chunk_index INTEGER NOT NULL, "
                "char_start INTEGER NOT NULL, "
                "char_end INTEGER NOT NULL, "
                "text TEXT NOT NULL, "
                "embedding_model VARCHAR(200) NOT NULL, "
                "embedding_dim INTEGER NOT NULL, "
                "created_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO cases (case_id, title, created_at, import_status) "
                "VALUES ('case-a', 'Old case', '2024-01-01 00:00:00', 'ready')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evidence_records ("
                "case_id, record_id, source_file, source_locator, content, "
                "content_hash, created_at) "
                "VALUES ('case-a', 'rec-1', 'a.txt', 'entire file', 'hello', 'abc', "
                "'2024-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evidence_chunks "
                "(chunk_id, case_id, record_id, chunk_index, char_start, char_end, text, "
                "embedding_model, embedding_dim, created_at) "
                "VALUES ('chunk-1', 'case-a', 'rec-1', 0, 0, 5, 'hello', 'old-model', 8, "
                "'2024-01-01 00:00:00')"
            )
        )
        cols = {row[1] for row in connection.execute(text("PRAGMA table_info(evidence_chunks)"))}
        assert "embedding_coverage" not in cols

    init_db(engine, FakeEmbedder().dim)

    with engine.connect() as connection:
        cols = {row[1] for row in connection.execute(text("PRAGMA table_info(evidence_chunks)"))}
    assert "embedding_coverage" in cols

    with make_session_factory(engine)() as session:
        chunk = session.scalar(select(EvidenceChunk).where(EvidenceChunk.chunk_id == "chunk-1"))
        assert chunk is not None
        assert chunk.text == "hello"
        assert chunk.embedding_coverage is None
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

        def embed_media(self, items):  # noqa: ANN001
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


def test_email_jpeg_filename_stores_as_jpg(tmp_path: Path) -> None:
    """Attachments named photo.jpeg must land as .jpg so media_path matches the schema."""
    assert extension_for_content_type("image/jpeg", "photo.jpeg") == ".jpg"

    msg = EmailMessage()
    msg["From"] = "Alex <alex@example.test>"
    msg["To"] = "Jordan <jordan@example.test>"
    msg["Date"] = DATE
    msg["Subject"] = "JPEG name"
    msg["Message-ID"] = "<jpeg@example.test>"
    msg.set_content("See the photo.")
    msg.add_attachment(JPEG_BYTES, maintype="image", subtype="jpeg", filename="photo.jpeg")

    path = write_bytes(tmp_path, "mail.eml", msg.as_bytes())
    media_dir = tmp_path / "media"
    records = {
        r.source_locator: r
        for r in import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    }
    image = records["part 2"]
    assert image.media_path == f"media/{image.record_id}.jpg"
    assert image.content_type == "image/jpeg"
    assert (media_dir / f"{image.record_id}.jpg").read_bytes() == JPEG_BYTES
    assert_schema(image)


def test_failed_eml_part_cleans_up_written_attachment(tmp_path: Path) -> None:
    """A later bad text part must not leave the earlier PNG attachment on disk."""
    # Build a multipart manually so the second text part has an impossible charset.
    raw = (
        b"From: Alex <alex@example.test>\r\n"
        b"To: Jordan <jordan@example.test>\r\n"
        b"Date: Fri, 10 Apr 2026 09:15:00 -0400\r\n"
        b"Subject: Partial fail\r\n"
        b"Message-ID: <partial@example.test>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="bound"\r\n'
        b"\r\n"
        b"--bound\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b'Content-Disposition: attachment; filename="dock.png"\r\n'
        b"\r\n"
    )
    import base64

    raw += base64.b64encode(PNG_BYTES) + b"\r\n"
    raw += (
        b"--bound\r\n"
        b"Content-Type: text/plain; charset=x-no-such-charset\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n"
        b"body that cannot decode\r\n"
        b"--bound--\r\n"
    )
    write_bytes(tmp_path, "mail.eml", raw)
    media_dir = tmp_path / "media"
    result = import_path(tmp_path, "case-a", media_dir=media_dir)
    assert result.errors
    assert "mail.eml" in {e.source_file for e in result.errors}
    leftover = list(media_dir.rglob("*.png")) if media_dir.exists() else []
    assert leftover == []
    assert result.written_media == {}


def test_oversized_loose_media_rejected_before_read(tmp_path: Path, monkeypatch) -> None:
    # Cap the limit tiny so we do not allocate a multi-GB fixture; the production
    # check still runs via file_path.stat().st_size before read_bytes.
    monkeypatch.setattr("app.evidence.importer.MAX_MEDIA_BYTES", 10)
    path = write_bytes(tmp_path, "huge.png", PNG_BYTES)  # PNG_BYTES is > 10 bytes
    media_dir = tmp_path / "media"
    with pytest.raises(EvidenceImportError, match="media limit"):
        import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)


def _email_with_png_attachment(
    *,
    png_b64: str,
    content_length: str | None,
    filename: str = "huge.png",
) -> bytes:
    if content_length is not None:
        length_header = f"Content-Length: {content_length}\r\n".encode("ascii")
    else:
        length_header = b""
    return (
        b"From: Alex <alex@example.test>\r\n"
        b"To: Jordan <jordan@example.test>\r\n"
        b"Date: Fri, 10 Apr 2026 09:15:00 -0400\r\n"
        b"Subject: Huge\r\n"
        b"Message-ID: <huge@example.test>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="bound"\r\n'
        b"\r\n"
        b"--bound\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Body survives.\r\n"
        b"--bound\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        + length_header
        + f'Content-Disposition: attachment; filename="{filename}"\r\n'.encode("ascii")
        + b"\r\n"
        + png_b64.encode("ascii")
        + b"\r\n"
        + b"--bound--\r\n"
    )


def _spy_image_decode_calls(monkeypatch) -> dict[str, int]:
    decoded_image_calls = {"n": 0}
    real_get_payload = EmailMessage.get_payload

    def spied_get_payload(self, *args, **kwargs):  # noqa: ANN001
        decode = kwargs.get("decode", args[0] if args else False)
        if decode and self.get_content_type() == "image/png":
            decoded_image_calls["n"] += 1
        return real_get_payload(self, *args, **kwargs)

    monkeypatch.setattr(EmailMessage, "get_payload", spied_get_payload)
    return decoded_image_calls


def test_oversized_email_image_rejected_before_decode(tmp_path: Path, monkeypatch) -> None:
    """Oversized body estimate rejects before decode; inflated Content-Length is ignored."""
    import base64

    from app.evidence import importer as importer_mod

    monkeypatch.setattr(importer_mod, "MAX_MEDIA_BYTES", 10)
    # Real PNG is already > 10 bytes; reject via body estimate. Content-Length may
    # also be large, but size_bytes must report the body estimate, not the header.
    png_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
    body_estimate = (sum(1 for c in png_b64 if not c.isspace()) * 3) // 4
    assert body_estimate > 10

    path = write_bytes(
        tmp_path,
        "huge.eml",
        _email_with_png_attachment(png_b64=png_b64, content_length="99999"),
    )
    media_dir = tmp_path / "media"
    decoded_image_calls = _spy_image_decode_calls(monkeypatch)

    sink = ImportSink()
    records = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir, sink=sink)
    assert [r.source_locator for r in records] == ["headers", "part 1"]
    assert records[1].content == "Body survives."
    assert records[0].metadata["attachments"][0]["filename"] == "huge.png"
    assert records[0].metadata["attachments"][0]["size_bytes"] == body_estimate
    assert sink.warnings
    assert "media limit" in sink.warnings[0].reason
    assert decoded_image_calls["n"] == 0
    assert not media_dir.exists() or list(media_dir.iterdir()) == []


def test_inflated_content_length_small_png_still_stored(tmp_path: Path, monkeypatch) -> None:
    """PNG under the cap with Content-Length over it must still import; size is the body."""
    import base64

    from app.evidence import importer as importer_mod

    # Cap above the real PNG (~67 bytes) but below the lying Content-Length.
    monkeypatch.setattr(importer_mod, "MAX_MEDIA_BYTES", 100)
    png_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
    body_estimate = (sum(1 for c in png_b64 if not c.isspace()) * 3) // 4
    assert body_estimate < 100 < 99999

    path = write_bytes(
        tmp_path,
        "lie-big.eml",
        _email_with_png_attachment(png_b64=png_b64, content_length="99999", filename="dock.png"),
    )
    media_dir = tmp_path / "media"
    records = {
        r.source_locator: r
        for r in import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    }

    assert set(records) == {"headers", "part 1", "part 2"}
    image = records["part 2"]
    assert image.modality == "image"
    assert image.content_type == "image/png"
    assert (media_dir / f"{image.record_id}.png").read_bytes() == PNG_BYTES
    # Under the cap we decode for the exact length; must not use the inflated header.
    assert records["headers"].metadata["attachments"][0]["size_bytes"] == len(PNG_BYTES)
    assert records["headers"].metadata["attachments"][0]["size_bytes"] != 99999


def test_tiny_content_length_huge_base64_rejected_before_decode(
    tmp_path: Path, monkeypatch
) -> None:
    """A small Content-Length must not skip the gate when the base64 body is huge."""
    import base64

    from app.evidence import importer as importer_mod

    monkeypatch.setattr(importer_mod, "MAX_MEDIA_BYTES", 10)
    # Body estimate from base64 is ~len(PNG_BYTES) (> 10); Content-Length lies small.
    # Old hint trusted Content-Length and would have called get_payload(decode=True).
    png_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
    body_estimate = (sum(1 for c in png_b64 if not c.isspace()) * 3) // 4
    assert body_estimate > 10

    path = write_bytes(
        tmp_path,
        "lie.eml",
        _email_with_png_attachment(png_b64=png_b64, content_length="1"),
    )
    media_dir = tmp_path / "media"
    decoded_image_calls = _spy_image_decode_calls(monkeypatch)

    sink = ImportSink()
    records = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir, sink=sink)
    assert [r.source_locator for r in records] == ["headers", "part 1"]
    assert records[1].content == "Body survives."
    assert records[0].metadata["attachments"][0]["size_bytes"] == body_estimate
    assert sink.warnings
    assert "media limit" in sink.warnings[0].reason
    assert decoded_image_calls["n"] == 0
    assert not media_dir.exists() or list(media_dir.iterdir()) == []


def test_store_media_rejects_oversized_bytes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.case_media.MAX_MEDIA_BYTES", 10)
    media_dir = tmp_path / "media"
    record_id = stable_record_id("case-a", "shot.png", "entire file")
    with pytest.raises(MediaStoreError, match="limit"):
        store_media_bytes(media_dir, record_id, b"x" * 11, ".png")


def test_pydantic_requires_media_fields_when_modality_is_image() -> None:
    with pytest.raises(ValidationError, match="media_path and content_type"):
        EvidenceRecord(
            record_id="rec_0123456789abcdef",
            case_id="case-a",
            source_file="shot.png",
            source_locator="entire file",
            content="",
            modality="image",
        )


def test_cli_reimport_embedding_failure_keeps_existing_media(tmp_path: Path) -> None:
    """Failed re-import must not delete media that an earlier successful import created."""
    from sqlalchemy import select

    from app.db import make_engine, make_session_factory
    from app.evidence.__main__ import main
    from app.models import Case

    exports = tmp_path / "exports"
    exports.mkdir()
    write_bytes(exports, "shot.png", PNG_BYTES)
    db = tmp_path / "blanc.db"
    media_dir = tmp_path / "media"

    assert (
        main(
            [
                str(exports),
                "--store",
                "--title",
                "Keep media",
                "--database-url",
                f"sqlite:///{db}",
                "--media-dir",
                str(media_dir),
            ],
            embedder=FakeEmbedder(),
        )
        == 0
    )
    pngs = list(media_dir.glob("*.png"))
    assert len(pngs) == 1
    existing = pngs[0]
    before = existing.read_bytes()

    class BoomEmbedder(FakeEmbedder):
        def embed_documents(self, texts):  # noqa: ANN001
            raise EmbeddingError("boom")

    engine = make_engine(f"sqlite:///{db}")
    with make_session_factory(engine)() as session:
        case_id = session.scalars(select(Case.case_id)).one()
    engine.dispose()

    # Add new text so embedding runs; the PNG is an idempotent no-op (created=False)
    # and must not be unlinked when embedding fails.
    (exports / "notes.txt").write_text("hello\n", encoding="utf-8")
    code = main(
        [
            str(exports),
            "--store",
            "--case-id",
            case_id,
            "--database-url",
            f"sqlite:///{db}",
            "--media-dir",
            str(media_dir),
        ],
        embedder=BoomEmbedder(),
    )
    assert code == 1
    assert existing.exists()
    assert existing.read_bytes() == before


def test_cli_empty_records_cleanup_leaves_no_media(tmp_path: Path, monkeypatch) -> None:
    """CLI finally must unlink media when import returns written files but no records."""
    from app.evidence import __main__ as cli
    from app.evidence.importer import ImportResult

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    orphan = media_dir / "rec_0123456789abcdef.png"
    orphan.write_bytes(PNG_BYTES)

    def fake_import(args, case_id):  # noqa: ANN001, ARG001
        return ImportResult(written_media={"rec_0123456789abcdef": orphan})

    monkeypatch.setattr(cli, "_import", fake_import)
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "notes.txt").write_text("unused\n", encoding="utf-8")
    db = tmp_path / "blanc.db"
    code = cli.main(
        [
            str(exports),
            "--store",
            "--title",
            "Empty",
            "--database-url",
            f"sqlite:///{db}",
            "--media-dir",
            str(media_dir),
        ],
        embedder=FakeEmbedder(),
    )
    assert code == 1
    assert not orphan.exists()


def test_strict_store_cleans_media_from_earlier_files(tmp_path: Path) -> None:
    """--strict must not leave media from files that imported before the failure."""
    import base64

    from app.evidence.__main__ import main

    exports = tmp_path / "exports"
    exports.mkdir()
    write_bytes(exports, "a.png", PNG_BYTES)
    raw = (
        b"From: Alex <alex@example.test>\r\n"
        b"To: Jordan <jordan@example.test>\r\n"
        b"Date: Fri, 10 Apr 2026 09:15:00 -0400\r\n"
        b"Subject: Strict fail\r\n"
        b"Message-ID: <strict@example.test>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="bound"\r\n'
        b"\r\n"
        b"--bound\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b'Content-Disposition: attachment; filename="dock.png"\r\n'
        b"\r\n"
    )
    raw += base64.b64encode(PNG_BYTES) + b"\r\n"
    raw += (
        b"--bound\r\n"
        b"Content-Type: text/plain; charset=x-no-such-charset\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n"
        b"body that cannot decode\r\n"
        b"--bound--\r\n"
    )
    write_bytes(exports, "b.eml", raw)
    db = tmp_path / "blanc.db"
    media_dir = tmp_path / "media"
    code = main(
        [
            str(exports),
            "--store",
            "--strict",
            "--title",
            "Strict orphan",
            "--database-url",
            f"sqlite:///{db}",
            "--media-dir",
            str(media_dir),
        ],
        embedder=FakeEmbedder(),
    )
    assert code == 1
    leftover = list(media_dir.rglob("*.png")) if media_dir.exists() else []
    assert leftover == []


def test_import_path_unexpected_error_cleans_earlier_media(tmp_path: Path, monkeypatch) -> None:
    """A non-EvidenceImportError abort must still drop earlier files' media."""
    from app.evidence import importer as imp

    write_bytes(tmp_path, "a.png", PNG_BYTES)
    write_bytes(tmp_path, "b.png", PNG_BYTES)
    media_dir = tmp_path / "media"
    calls = {"n": 0}
    real = imp.import_file

    def wrapped(*args, **kwargs):  # noqa: ANN002, ANN003
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(imp, "import_file", wrapped)
    with pytest.raises(RuntimeError, match="boom"):
        import_path(tmp_path, "case-a", media_dir=media_dir)
    leftover = list(media_dir.rglob("*.png")) if media_dir.exists() else []
    assert leftover == []


def test_media_build_failure_unlinks_written_file(tmp_path: Path, monkeypatch) -> None:
    """If EvidenceRecord build fails after store_media_bytes, the file must go."""
    from app.evidence import importer as imp

    path = write_bytes(tmp_path, "shot.png", PNG_BYTES)
    media_dir = tmp_path / "media"
    real_build = imp._RecordBuilder.build

    def boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if kwargs.get("modality") == "image":
            raise ValidationError.from_exception_data(
                "EvidenceRecord",
                [{"type": "missing", "loc": ("media_path",), "input": {}}],
            )
        return real_build(self, *args, **kwargs)

    monkeypatch.setattr(imp._RecordBuilder, "build", boom)
    with pytest.raises(ValidationError):
        import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    leftover = list(media_dir.rglob("*")) if media_dir.exists() else []
    assert leftover == []


def test_email_image_build_failure_falls_back_without_orphan(tmp_path: Path, monkeypatch) -> None:
    """Attachment build failure after write → metadata-only warning, no leftover file."""
    from app.evidence import importer as imp

    msg = EmailMessage()
    msg["From"] = "Alex <alex@example.test>"
    msg["To"] = "Jordan <jordan@example.test>"
    msg["Date"] = DATE
    msg["Subject"] = "Build fail"
    msg["Message-ID"] = "<build@example.test>"
    msg.set_content("Body survives.")
    msg.add_attachment(PNG_BYTES, maintype="image", subtype="png", filename="dock.png")
    path = write_bytes(tmp_path, "mail.eml", msg.as_bytes())
    media_dir = tmp_path / "media"
    real_build = imp._RecordBuilder.build

    def boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if kwargs.get("modality") == "image":
            raise ValidationError.from_exception_data(
                "EvidenceRecord",
                [{"type": "missing", "loc": ("media_path",), "input": {}}],
            )
        return real_build(self, *args, **kwargs)

    monkeypatch.setattr(imp._RecordBuilder, "build", boom)
    sink = ImportSink()
    records = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir, sink=sink)
    assert [r.source_locator for r in records] == ["headers", "part 1"]
    assert sink.warnings
    assert "metadata only" in sink.warnings[0].reason
    leftover = list(media_dir.rglob("*.png")) if media_dir.exists() else []
    assert leftover == []


def test_verify_media_bytes_rejects_oversized(monkeypatch) -> None:
    from app.case_media import verify_media_bytes

    monkeypatch.setattr("app.case_media.MAX_MEDIA_BYTES", 10)
    with pytest.raises(MediaStoreError, match="limit"):
        verify_media_bytes(b"x" * 11, "image/png")


def test_import_path_validation_error_cleans_media(tmp_path: Path, monkeypatch) -> None:
    """ValidationError during a folder import must not leave that file's media behind."""
    from app.evidence import importer as imp

    write_bytes(tmp_path, "shot.png", PNG_BYTES)
    media_dir = tmp_path / "media"
    real_build = imp._RecordBuilder.build

    def boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if kwargs.get("modality") == "image":
            raise ValidationError.from_exception_data(
                "EvidenceRecord",
                [{"type": "missing", "loc": ("media_path",), "input": {}}],
            )
        return real_build(self, *args, **kwargs)

    monkeypatch.setattr(imp._RecordBuilder, "build", boom)
    result = import_path(tmp_path, "case-a", media_dir=media_dir)
    assert result.errors
    assert "invalid evidence record" in result.errors[0].reason
    leftover = list(media_dir.rglob("*")) if media_dir.exists() else []
    assert leftover == []


def test_cli_rejected_media_record_cleans_up_new_file(tmp_path: Path) -> None:
    """Media written for a record that store_records rejects must be unlinked."""
    from app.evidence.__main__ import main

    db = tmp_path / "blanc.db"
    media_dir = tmp_path / "media"
    exports = tmp_path / "exports"
    exports.mkdir()

    case_id = "case_rejectedmedia01"
    record_id = stable_record_id(case_id, "shot.png", "entire file")
    engine = make_engine(f"sqlite:///{db}")
    init_db(engine, FakeEmbedder().dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id=case_id, title="Reject media"))
        session.flush()
        # Same record_id the PNG import will use, but different immutable content.
        placeholder = EvidenceRecord(
            record_id=record_id,
            case_id=case_id,
            source_file="shot.png",
            source_locator="entire file",
            content="placeholder text, not the image",
        )
        assert store_records(session, case_id, [placeholder], FakeEmbedder()).inserted == 1
        session.commit()
    engine.dispose()

    write_bytes(exports, "shot.png", PNG_BYTES)
    code = main(
        [
            str(exports),
            "--store",
            "--case-id",
            case_id,
            "--database-url",
            f"sqlite:///{db}",
            "--media-dir",
            str(media_dir),
        ],
        embedder=FakeEmbedder(),
    )
    assert code == 1
    leftover = list(media_dir.rglob("*.png")) if media_dir.exists() else []
    assert leftover == []


def test_media_reembed_on_model_change_uses_bytes_not_sidecar(tmp_path: Path) -> None:
    """A model switch must call embed_media with file bytes, never the FTS sidecar text."""
    from app.evidence_store import list_chunks, reembed_stale_chunks, store_records

    path = write_bytes(tmp_path, "shot.png", PNG_BYTES)
    media_dir = tmp_path / "media"
    (record,) = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    embedder = FakeEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'reembed.db'}")
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="Media"))
        session.flush()
        store_records(session, "case-a", [record], embedder, media_dir=media_dir)
        session.commit()
        sidecar = list_chunks(session, "case-a", record.record_id)[0].text
        embedder.calls.clear()
        embedder.media_calls.clear()
        new = FakeEmbedder(model="fake-embedding-v2")
        assert reembed_stale_chunks(session, "case-a", new, media_dir=media_dir) == 1
        session.commit()
        assert new.embedded_media and new.embedded_media[0].data == PNG_BYTES
        assert new.embedded_media[0].mime_type == "image/png"
        assert sidecar not in new.embedded_texts
        assert new.calls == []  # text path must not run for media
        assert list_chunks(session, "case-a", record.record_id)[0].embedding_model == new.model
    engine.dispose()


def test_media_backfill_creates_chunk_and_fts(tmp_path: Path) -> None:
    """Media imported before BLA-69 (row, no chunks) gets a sidecar chunk + FTS on reembed."""
    from app.evidence_store import (
        content_hash,
        list_chunks,
        reembed_stale_chunks,
        stale_chunk_count,
    )
    from app.models import EvidenceRecordRow
    from app.search_index import FTS_TABLE, VEC_TABLE, keyword_search, to_fts_query

    path = write_bytes(tmp_path, "legacy.png", PNG_BYTES)
    media_dir = tmp_path / "media"
    (record,) = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    embedder = FakeEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="Legacy media"))
        session.flush()
        # Simulate a pre-BLA-69 import: record row only, no chunks / indexes.
        session.add(
            EvidenceRecordRow(
                case_id="case-a",
                record_id=record.record_id,
                source_file=record.source_file,
                source_locator=record.source_locator,
                content="",
                modality=record.modality,
                media_path=record.media_path,
                content_type=record.content_type,
                record_metadata=record.metadata,
                content_hash=content_hash(record),
            )
        )
        session.commit()
        assert list_chunks(session, "case-a", record.record_id) == []
        assert stale_chunk_count(session, "case-a", embedder, media_dir=media_dir) == 1
        assert reembed_stale_chunks(session, "case-a", embedder, media_dir=media_dir) == 1
        session.commit()
        chunks = list_chunks(session, "case-a", record.record_id)
        assert len(chunks) == 1
        assert chunks[0].text == "legacy.png"
        assert session.execute(text(f"SELECT count(*) FROM {VEC_TABLE}")).scalar() == 1
        assert session.execute(text(f"SELECT count(*) FROM {FTS_TABLE}")).scalar() == 1
        hits = keyword_search(session, "case-a", to_fts_query("legacy"))
        assert [h.record_id for h in hits] == [record.record_id]
        assert embedder.embedded_media and embedder.embedded_media[0].data == PNG_BYTES
    engine.dispose()


def test_media_is_retrieved_without_a_text_excerpt(tmp_path: Path) -> None:
    """BLA-70: media ranks with text and enters the window as a record with no excerpt;
    exclusion still drops it."""
    from app.evidence_store import list_chunks, store_records
    from app.retrieval import retrieve
    from app.search_index import keyword_search, to_fts_query, vector_search

    media_dir = tmp_path / "media"
    png_path = write_bytes(tmp_path, "uniquezebra.png", PNG_BYTES)
    (media,) = import_file(png_path, "case-a", source_root=tmp_path, media_dir=media_dir)
    text_record = EvidenceRecord.model_validate(
        {
            "record_id": "rec-text",
            "case_id": "case-a",
            "source_file": "notes.txt",
            "source_locator": "line 1",
            "content": "Alex emails Jordan about the warehouse key.",
        }
    )
    embedder = FakeEmbedder()
    # Point the query vector at the media bytes so vector search would otherwise hit it.
    media_vec = embedder.embed_media([MediaInput(PNG_BYTES, "image/png")])[0]
    embedder.media_calls.clear()

    class QueryEmbedder(FakeEmbedder):
        def embed_query(self, text: str) -> list[float]:  # noqa: ANN001
            if "uniquezebra" in text:
                return media_vec
            return super().embed_query(text)

    query_embedder = QueryEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'retrieve-media.db'}")
    init_db(engine, query_embedder.dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="Filter media"))
        session.flush()
        store_records(session, "case-a", [text_record, media], query_embedder, media_dir=media_dir)
        session.commit()
        assert list_chunks(session, "case-a", media.record_id)
        assert keyword_search(session, "case-a", to_fts_query("uniquezebra"))
        assert vector_search(session, "case-a", media_vec, k=5)
        # Ranked first (vector + filename keyword match), with no fake text excerpt.
        result = retrieve(session, "case-a", "uniquezebra", query_embedder)
        top = result.records[0]
        assert (top.record_id, top.modality, top.matched_by) == (media.record_id, "image", "both")
        assert top.excerpts == []  # the FTS sidecar is never shown as record content
        assert (top.media_path, top.content_type) == (media.media_path, "image/png")
        assert result.excerpt_chars == sum(
            len(e.text) for r in result.records for e in r.excerpts
        )  # media costs no character budget
        # Exclusion path: even if media were otherwise a candidate, excluded ids stay out.
        excluded = retrieve(
            session,
            "case-a",
            "uniquezebra warehouse",
            query_embedder,
            excluded_record_ids={media.record_id},
        )
        assert media.record_id not in {r.record_id for r in excluded.records}
        assert media.record_id not in {
            h.record_id
            for h in keyword_search(
                session,
                "case-a",
                to_fts_query("uniquezebra"),
                excluded_record_ids={media.record_id},
            )
        }
        assert media.record_id not in {
            h.record_id
            for h in vector_search(
                session, "case-a", media_vec, k=5, excluded_record_ids={media.record_id}
            )
        }
    engine.dispose()

def test_pdf_six_vs_seven_pages_and_truncation_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    from app.case_media import GEMINI_PDF_MAX_PAGES, truncate_pdf_for_embedding

    del tmp_path

    def pdf_with_pages(n: int) -> bytes:
        writer = PdfWriter()
        for _ in range(n):
            writer.add_blank_page(width=72, height=72)
        buf = BytesIO()
        writer.write(buf)
        return buf.getvalue()

    six = pdf_with_pages(GEMINI_PDF_MAX_PAGES)
    seven = pdf_with_pages(GEMINI_PDF_MAX_PAGES + 1)
    with caplog.at_level("WARNING", logger="app.case_media"):
        short = truncate_pdf_for_embedding(six)
        assert short.data == six and short.truncated is False
        assert not any("truncated" in m.message.lower() for m in caplog.records)
        truncated = truncate_pdf_for_embedding(seven)
    assert truncated.truncated is True
    assert truncated.coverage_metadata() == {
        "embedding_truncated": True,
        "embedding_pages": GEMINI_PDF_MAX_PAGES,
        "embedding_page_count": GEMINI_PDF_MAX_PAGES + 1,
    }
    assert len(PdfReader(BytesIO(truncated.data)).pages) == GEMINI_PDF_MAX_PAGES
    assert any("truncated" in m.message.lower() for m in caplog.records)

    with caplog.at_level("WARNING", logger="app.case_media"):
        caplog.clear()
        failed = truncate_pdf_for_embedding(b"%PDF-not-a-real-pdf")
    assert failed.data == b"%PDF-not-a-real-pdf" and failed.truncated is False
    assert any("truncation failed" in m.message.lower() for m in caplog.records)


def test_seven_page_pdf_store_and_reembed_sets_truncation_flag(tmp_path: Path) -> None:
    """Truncation lives on the PDF chunk's embedding_coverage, not the evidence record."""
    from io import BytesIO

    from pypdf import PdfWriter

    from app.case_media import GEMINI_PDF_MAX_PAGES
    from app.evidence_store import get_record, list_chunks, reembed_stale_chunks, store_records

    writer = PdfWriter()
    for _ in range(GEMINI_PDF_MAX_PAGES + 1):
        writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    seven_pdf = buf.getvalue()
    path = write_bytes(tmp_path, "long.pdf", seven_pdf)
    media_dir = tmp_path / "media"
    (record,) = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    assert "embedding_truncated" not in (record.metadata or {})

    embedder = FakeEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'pdf-flag.db'}")
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="PDF flag"))
        session.flush()
        store_records(session, "case-a", [record], embedder, media_dir=media_dir)
        session.commit()
        stored = get_record(session, "case-a", record.record_id)
        assert stored is not None
        # GET /records/{id} must not expose index coverage as evidence metadata.
        assert "embedding_truncated" not in (stored.metadata or {})
        chunks = list_chunks(session, "case-a", record.record_id)
        assert len(chunks) == 1
        assert chunks[0].embedding_coverage == {
            "embedding_truncated": True,
            "embedding_pages": GEMINI_PDF_MAX_PAGES,
            "embedding_page_count": GEMINI_PDF_MAX_PAGES + 1,
        }
        # Re-import still matches (coverage is not on the record / content_hash).
        again = store_records(session, "case-a", [record], embedder, media_dir=media_dir)
        assert again.unchanged == 1 and again.inserted == 0
        # Re-embed keeps the flag on the chunk; record metadata stays untouched.
        new = FakeEmbedder(model="fake-embedding-v2")
        assert reembed_stale_chunks(session, "case-a", new, media_dir=media_dir) == 1
        session.commit()
        reembedded = get_record(session, "case-a", record.record_id)
        assert reembedded is not None
        assert "embedding_truncated" not in (reembedded.metadata or {})
        rechunks = list_chunks(session, "case-a", record.record_id)
        assert rechunks[0].embedding_coverage == {
            "embedding_truncated": True,
            "embedding_pages": GEMINI_PDF_MAX_PAGES,
            "embedding_page_count": GEMINI_PDF_MAX_PAGES + 1,
        }
    engine.dispose()


def test_stale_chunk_count_includes_media(tmp_path: Path) -> None:
    from app.evidence_store import (
        content_hash,
        reembed_stale_chunks,
        stale_chunk_count,
        store_records,
    )
    from app.models import EvidenceRecordRow

    media_dir = tmp_path / "media"
    path = write_bytes(tmp_path, "shot.png", PNG_BYTES)
    (media,) = import_file(path, "case-a", source_root=tmp_path, media_dir=media_dir)
    text = EvidenceRecord.model_validate(
        {
            "record_id": "rec-text",
            "case_id": "case-a",
            "source_file": "notes.txt",
            "source_locator": "line 1",
            "content": "short note",
        }
    )
    embedder = FakeEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'stale-media.db'}")
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="Stale media"))
        session.flush()
        store_records(session, "case-a", [text, media], embedder, media_dir=media_dir)
        session.commit()
        assert stale_chunk_count(session, "case-a", embedder, media_dir=media_dir) == 0
        new = FakeEmbedder(model="fake-embedding-v2")
        # One text chunk + one media chunk both stale after the model change.
        assert stale_chunk_count(session, "case-a", new, media_dir=media_dir) == 2
        assert reembed_stale_chunks(session, "case-a", new, media_dir=media_dir) == 2

        # Pre-BLA-69 media (no chunk) also counts as stale.
        legacy_path = write_bytes(tmp_path, "legacy2.png", PNG_BYTES)
        (legacy,) = import_file(legacy_path, "case-a", source_root=tmp_path, media_dir=media_dir)
        session.add(
            EvidenceRecordRow(
                case_id="case-a",
                record_id=legacy.record_id,
                source_file=legacy.source_file,
                source_locator=legacy.source_locator,
                content="",
                modality=legacy.modality,
                media_path=legacy.media_path,
                content_type=legacy.content_type,
                record_metadata=legacy.metadata,
                content_hash=content_hash(legacy),
            )
        )
        session.commit()
        assert stale_chunk_count(session, "case-a", new, media_dir=media_dir) == 1
    engine.dispose()


def test_vector_index_uses_cosine_distance(tmp_path: Path) -> None:
    from app.search_index import VEC_TABLE

    embedder = FakeEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'cosine.db'}")
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        ddl = session.execute(
            text("SELECT sql FROM sqlite_master WHERE name = :name"),
            {"name": VEC_TABLE},
        ).scalar()
        assert ddl is not None
        assert "distance_metric=cosine" in ddl
    engine.dispose()


def test_reembed_continues_when_one_media_file_is_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from app.evidence_store import list_chunks, reembed_stale_chunks, store_records

    media_dir = tmp_path / "media"
    png = write_bytes(tmp_path, "keep.png", PNG_BYTES)
    gone = write_bytes(tmp_path, "gone.png", PNG_BYTES)
    keep = import_file(png, "case-a", source_root=tmp_path, media_dir=media_dir)[0]
    missing = import_file(gone, "case-a", source_root=tmp_path, media_dir=media_dir)[0]
    text = EvidenceRecord.model_validate(
        {
            "record_id": "rec-text",
            "case_id": "case-a",
            "source_file": "notes.txt",
            "source_locator": "line 1",
            "content": "text survives a missing media file",
        }
    )
    embedder = FakeEmbedder()
    engine = make_engine(f"sqlite:///{tmp_path / 'missing-media.db'}")
    init_db(engine, embedder.dim)
    with make_session_factory(engine)() as session:
        session.add(Case(case_id="case-a", title="Missing media"))
        session.flush()
        store_records(session, "case-a", [text, keep, missing], embedder, media_dir=media_dir)
        session.commit()
        (media_dir / f"{missing.record_id}.png").unlink()
        new = FakeEmbedder(model="fake-embedding-v2")
        with caplog.at_level("WARNING", logger="app.evidence_store"):
            updated = reembed_stale_chunks(session, "case-a", new, media_dir=media_dir)
        session.commit()
        # Text + keep.png re-embedded; missing media skipped with a warning.
        assert updated == 2
        assert any("skipping re-embed" in m.message for m in caplog.records)
        assert list_chunks(session, "case-a", keep.record_id)[0].embedding_model == new.model
        assert (
            list_chunks(session, "case-a", missing.record_id)[0].embedding_model == embedder.model
        )
    engine.dispose()
