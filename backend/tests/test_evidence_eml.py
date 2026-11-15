"""EML import (BLA-10): header and MIME-part locators, all-or-nothing failures.

Every message here is synthetic and built in the test.
"""

import json
from email.message import EmailMessage
from pathlib import Path

import pytest
from _paths import CONTRACTS_DIR, load_json
from jsonschema import Draft202012Validator

from app.evidence import EvidenceImportError, import_file, import_path

RECORD_SCHEMA = Draft202012Validator(load_json(CONTRACTS_DIR / "evidence_record.schema.json"))
DATE = "Fri, 10 Apr 2026 09:15:00 -0400"


def message(body: str = "Can you bring the documents on Friday?") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Alex Example <alex@example.test>"
    msg["To"] = "Jordan Example <jordan@example.test>"
    msg["Cc"] = "casey@example.test"
    msg["Date"] = DATE
    msg["Subject"] = "Delivery"
    msg["Message-ID"] = "<m1@example.test>"
    msg.set_content(body)
    return msg


def save(tmp_path: Path, name: str, data: bytes | EmailMessage) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.as_bytes())
    return path


def by_locator(path: Path, root: Path) -> dict:
    records = import_file(path, "case-a", source_root=root)
    for record in records:
        payload = record.model_dump(mode="json", exclude_none=True)
        assert not list(RECORD_SCHEMA.iter_errors(payload))
    return {record.source_locator: record for record in records}


def test_a_plain_email_gives_a_headers_record_and_a_body_record(tmp_path: Path) -> None:
    path = save(tmp_path, "mail/delivery.eml", message())
    records = by_locator(path, tmp_path)
    assert list(records) == ["headers", "body"]

    headers = records["headers"]
    assert headers.content.splitlines() == [
        "From: Alex Example <alex@example.test>",
        "To: Jordan Example <jordan@example.test>",
        "Cc: casey@example.test",
        f"Date: {DATE}",
        "Subject: Delivery",
        "Message-ID: <m1@example.test>",
    ]
    body = records["body"]
    assert body.content == "Can you bring the documents on Friday?"
    for record in (headers, body):
        assert record.source_file == "mail/delivery.eml"
        assert record.timestamp == DATE  # copied as written
        assert record.timestamp_kind == "sent"
        assert record.metadata["date_iso"] == "2026-04-10T09:15:00-04:00"
        assert record.metadata["subject"] == "Delivery"
        assert record.original_sha256
    assert body.metadata["content_type"] == "text/plain"

    # Record IDs are stable across imports.
    again = by_locator(path, tmp_path)
    assert [r.record_id for r in again.values()] == [r.record_id for r in records.values()]


def test_only_the_plain_text_alternative_is_kept(tmp_path: Path) -> None:
    msg = message("Plain version.")
    msg.add_alternative("<p>HTML <b>version</b>.</p>", subtype="html")
    records = by_locator(save(tmp_path, "a.eml", msg), tmp_path)
    assert list(records) == ["headers", "part 1"]
    assert records["part 1"].content == "Plain version."


def test_an_html_only_email_is_reduced_to_its_visible_text(tmp_path: Path) -> None:
    msg = message()
    msg.set_content(
        "<html><head><style>p {color: red}</style><title>t</title></head><body>"
        "<p>Meet at the <b>dock</b> &amp; wait.</p><script>alert(1)</script>"
        "<div>Second&nbsp;line</div></body></html>",
        subtype="html",
    )
    records = by_locator(save(tmp_path, "h.eml", msg), tmp_path)
    assert records["body"].content == "Meet at the dock & wait.\nSecond line"
    assert records["body"].metadata["content_type"] == "text/html"


def test_parts_are_numbered_and_attachments_are_listed(tmp_path: Path) -> None:
    msg = message("See the attached notes.")
    msg.add_alternative("<p>See the attached notes.</p>", subtype="html")
    msg.add_attachment(b"%PDF-1.7 fake", maintype="application", subtype="pdf", filename="scan.pdf")
    msg.add_attachment("Pickup list: crates 1-40.", filename="list.txt")
    records = by_locator(save(tmp_path, "m.eml", msg), tmp_path)

    # mixed[alternative[plain, html], pdf, text] -> part 1.1, (pdf = part 2), part 3
    assert list(records) == ["headers", "part 1.1", "part 3"]
    assert records["part 1.1"].content == "See the attached notes."
    assert records["part 3"].content == "Pickup list: crates 1-40."
    assert records["part 3"].metadata["filename"] == "list.txt"
    # Non-text attachments are listed in metadata, never added to the source text.
    assert records["headers"].metadata["attachments"] == [
        {
            "locator": "part 2",
            "filename": "scan.pdf",
            "content_type": "application/pdf",
            "size_bytes": 13,
        }
    ]
    assert "scan.pdf" not in records["headers"].content
    assert records["headers"].content.splitlines()[-1] == "Message-ID: <m1@example.test>"


def test_encoded_headers_and_bodies_are_decoded(tmp_path: Path) -> None:
    raw = (
        "From: =?utf-8?q?Zo=C3=AB?= <zoe@example.test>\r\n"
        "To: jordan@example.test\r\n"
        f"Date: {DATE}\r\n"
        "Subject: =?utf-8?b?Q2Fmw6kgbWVldGluZw==?=\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=iso-8859-1\r\n"
        "Content-Transfer-Encoding: quoted-printable\r\n"
        "\r\n"
        "Rendez-vous au caf=E9 =E0 10h.\r\n"
    ).encode("ascii")
    records = by_locator(save(tmp_path, "enc.eml", raw), tmp_path)
    assert "From: Zoë <zoe@example.test>" in records["headers"].content
    assert "Subject: Café meeting" in records["headers"].content
    assert records["body"].content == "Rendez-vous au café à 10h."
    assert records["body"].metadata["charset"] == "iso-8859-1"


def forwarded(inner: EmailMessage) -> EmailMessage:
    outer = message("Forwarding this.")
    outer.replace_header("Date", "Mon, 13 Apr 2026 08:00:00 +0000")
    outer.replace_header("Subject", "Fwd: Delivery")
    outer.add_attachment(inner)
    return outer


def test_a_forwarded_message_is_read_like_the_outer_one(tmp_path: Path) -> None:
    records = by_locator(save(tmp_path, "fwd.eml", forwarded(message("Original text."))), tmp_path)
    assert list(records) == ["headers", "part 1", "part 2 headers", "part 2.1"]
    assert records["part 2.1"].content == "Original text."
    assert "Subject: Delivery" in records["part 2 headers"].content
    # Each message keeps its own date and subject.
    assert records["part 1"].timestamp == "Mon, 13 Apr 2026 08:00:00 +0000"
    assert records["part 1"].metadata["subject"] == "Fwd: Delivery"
    for locator in ("part 2 headers", "part 2.1"):
        assert records[locator].timestamp == DATE
        assert records[locator].metadata["subject"] == "Delivery"


def test_a_forwarded_multipart_message_is_decoded_not_stored_as_raw_mime(
    tmp_path: Path,
) -> None:
    inner = message("See the attached scan.")
    inner.add_alternative("<p>See the attached <b>scan</b>.</p>", subtype="html")
    pdf = b"%PDF-1.7 fake"
    inner.add_attachment(pdf, maintype="application", subtype="pdf", filename="scan.pdf")
    inner.add_attachment("Caf\u00e9 list: crates 1-40.", filename="list.txt", cte="base64")
    records = by_locator(save(tmp_path, "fwd.eml", forwarded(inner)), tmp_path)

    # inner = mixed[alternative[plain, html], pdf, text] inside outer part 2
    assert list(records) == ["headers", "part 1", "part 2 headers", "part 2.1.1", "part 2.3"]
    assert records["part 2.1.1"].content == "See the attached scan."
    assert records["part 2.3"].content == "Caf\u00e9 list: crates 1-40."  # base64 decoded
    assert records["part 2 headers"].metadata["attachments"] == [
        {
            "locator": "part 2.2",
            "filename": "scan.pdf",
            "content_type": "application/pdf",
            "size_bytes": 13,
        }
    ]
    assert "attachments" not in records["headers"].metadata  # the outer message has none
    for record in records.values():
        assert "Content-Transfer-Encoding" not in record.content
        assert "--=" not in record.content and "JVBER" not in record.content  # no MIME/base64


def test_forwarded_messages_nested_too_deep_are_an_error(tmp_path: Path) -> None:
    nested = message("Innermost.")
    for _ in range(22):
        nested = forwarded(nested)
    with pytest.raises(EvidenceImportError, match="nests forwarded messages more than 20 deep"):
        import_file(save(tmp_path, "deep.eml", nested), "case-a", source_root=tmp_path)
    shallow = message("Innermost.")
    for _ in range(3):
        shallow = forwarded(shallow)
    records = by_locator(save(tmp_path, "ok.eml", shallow), tmp_path)
    assert records["part 2.2.2.1"].content == "Innermost."


def test_html_entities_are_decoded_exactly_once(tmp_path: Path) -> None:
    msg = message()
    # The visible text is "The code is &lt;b&gt; and A &amp; B", escaped once more in HTML.
    msg.set_content(
        "<p>The code is &amp;lt;b&amp;gt; and A &amp;amp; B. Caf&eacute; &#233;</p>",
        subtype="html",
    )
    records = by_locator(save(tmp_path, "esc.eml", msg), tmp_path)
    assert records["body"].content == "The code is &lt;b&gt; and A &amp; B. Café é"


def test_an_unparseable_date_is_kept_as_written(tmp_path: Path) -> None:
    # Written as raw bytes: EmailMessage itself drops a Date it cannot parse.
    raw = message().as_bytes().replace(DATE.encode(), b"sometime last week")
    records = by_locator(save(tmp_path, "d.eml", raw), tmp_path)
    assert "Date: sometime last week" in records["headers"].content
    assert records["body"].timestamp == "sometime last week"
    assert records["body"].metadata["date_unparsed"] is True
    assert "date_iso" not in records["body"].metadata


def test_a_file_that_is_not_an_email_is_a_clear_error(tmp_path: Path) -> None:
    path = save(tmp_path, "notes.eml", b"just some notes\nwith no headers\n")
    with pytest.raises(EvidenceImportError, match="does not look like an email message"):
        import_file(path, "case-a", source_root=tmp_path)


def test_an_undecodable_part_fails_the_whole_file(tmp_path: Path) -> None:
    raw = (
        "From: alex@example.test\r\n"
        "Subject: Two parts\r\n"
        'Content-Type: multipart/mixed; boundary="b"\r\n'
        "\r\n"
        "--b\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "This part is fine.\r\n"
        "--b\r\n"
        "Content-Type: text/plain; charset=x-no-such-charset\r\n\r\n"
        "This one is not.\r\n"
        "--b--\r\n"
    ).encode("ascii")
    path = save(tmp_path, "bad.eml", raw)
    expected = r"part 2 could not be decoded.*x-no-such-charset"
    with pytest.raises(EvidenceImportError, match=expected):
        import_file(path, "case-a", source_root=tmp_path)
    result = import_path(tmp_path, "case-a")
    assert result.records == []  # not a partial import
    assert [problem.source_file for problem in result.errors] == ["bad.eml"]
    assert "none of this file's records were imported" in result.errors[0].reason


def test_an_empty_eml_file_gives_no_records(tmp_path: Path) -> None:
    assert import_file(save(tmp_path, "empty.eml", b"  \n"), "case-a", source_root=tmp_path) == []


def test_folder_imports_pick_up_eml_files(tmp_path: Path) -> None:
    save(tmp_path, "mail/one.eml", message("First."))
    save(tmp_path, "mail/Two.EML", message("Second."))
    (tmp_path / "notes.txt").write_text("a note\n")
    result = import_path(tmp_path, "case-a")
    assert result.errors == [] and result.skipped == []
    bodies = sorted(r.content for r in result.records if r.source_locator == "body")
    assert bodies == ["First.", "Second."]
    assert json.dumps([r.model_dump(mode="json") for r in result.records])  # serializable
