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

from app.case_media import MediaStoreError, store_media_bytes
from app.evidence import EvidenceImportError, import_file, import_path
from app.evidence.records import stable_record_id

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
# Two-page-ish PDF markers (best-effort page count for locators).
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
    assert pdf_rec.source_locator == "pages 1-2"
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


def test_media_import_needs_media_dir(tmp_path: Path) -> None:
    path = write_bytes(tmp_path, "shot.png", PNG_BYTES)
    with pytest.raises(EvidenceImportError, match="needs media_dir"):
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
