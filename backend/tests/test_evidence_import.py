"""Tests for the evidence importer (BLA-9). All inputs are synthetic."""

import json
from pathlib import Path

import pytest

from app.evidence import (
    EvidenceImportError,
    EvidenceRecord,
    UnsupportedFormatError,
    import_file,
    import_path,
)
from app.evidence.__main__ import main as cli_main

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (REPO_ROOT / "contracts" / "evidence_record.schema.json").read_text(encoding="utf-8")
)


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def assert_matches_schema(record: EvidenceRecord) -> None:
    """Cheap structural check against the shared contract (no jsonschema dependency)."""
    data = record.model_dump()
    assert set(data) <= set(SCHEMA["properties"])
    for key in SCHEMA["required"]:
        assert key in data
    for key in ("record_id", "case_id", "source_file", "source_locator"):
        assert isinstance(data[key], str) and data[key]
    assert data["timestamp_kind"] in SCHEMA["properties"]["timestamp_kind"]["enum"]


# --- contract sync -------------------------------------------------------------


def test_model_fields_match_the_contract() -> None:
    assert set(EvidenceRecord.model_fields) == set(SCHEMA["properties"])
    required = {name for name, info in EvidenceRecord.model_fields.items() if info.is_required()}
    assert required == set(SCHEMA["required"])
    kinds = EvidenceRecord.model_fields["timestamp_kind"].annotation.__args__
    assert set(kinds) == set(SCHEMA["properties"]["timestamp_kind"]["enum"])


# --- TXT -----------------------------------------------------------------------


def test_txt_paragraphs_become_records_with_line_locators(tmp_path: Path) -> None:
    path = write(tmp_path, "notes.txt", "first line\nsecond line\n\n\nthird block\n")
    records = import_file(path, "case-a")
    assert [r.source_locator for r in records] == ["lines 1-2", "line 5"]
    assert records[0].content == "first line\nsecond line"
    assert records[1].content == "third block"
    for record in records:
        assert record.case_id == "case-a"
        assert record.source_file == "notes.txt"
        assert record.timestamp is None and record.timestamp_kind == "unknown"
        assert_matches_schema(record)


def test_txt_line_mode_reads_a_log_and_keeps_the_timestamp_as_written(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "app.txt",
        "2026-04-10 09:15:00 user logged in\n\n[2026-04-10T09:20:00Z] file opened\nno time here\n",
    )
    records = import_file(path, "case-a", txt_mode="line")
    assert [r.source_locator for r in records] == ["line 1", "line 3", "line 4"]
    assert records[0].timestamp == "2026-04-10 09:15:00"
    assert records[1].timestamp == "2026-04-10T09:20:00Z"
    assert records[2].timestamp is None


def test_txt_file_mode_is_one_record(tmp_path: Path) -> None:
    path = write(tmp_path, "letter.txt", "Dear A,\n\nSee you Friday.\n")
    records = import_file(path, "case-a", txt_mode="file")
    assert len(records) == 1
    assert records[0].source_locator == "entire file"
    assert records[0].content == "Dear A,\n\nSee you Friday."


# --- JSON ----------------------------------------------------------------------


def test_json_array_yields_one_record_per_message(tmp_path: Path) -> None:
    messages = [
        {"author": "alex", "text": "meet at noon", "sent_at": "2026-04-10T09:15:00Z"},
        {"author": "jordan", "text": "ok", "timestamp": 1775812500},
        {"author": "casey", "text": "no time on this one"},
    ]
    path = write(tmp_path, "chat.json", json.dumps(messages))
    records = import_file(path, "case-a")
    assert [r.source_locator for r in records] == ["[0]", "[1]", "[2]"]
    assert records[0].content == "meet at noon"
    assert records[0].timestamp == "2026-04-10T09:15:00Z"
    assert records[0].timestamp_kind == "sent"
    assert records[0].metadata == {"author": "alex"}
    # Numeric timestamps are kept as written, never reinterpreted.
    assert records[1].timestamp == "1775812500"
    assert records[1].timestamp_kind == "unknown"
    assert records[1].metadata == {"author": "jordan", "timestamp_raw_type": "number"}
    assert records[2].timestamp is None and records[2].timestamp_kind == "unknown"
    for record in records:
        assert_matches_schema(record)


def test_json_object_with_a_message_list_uses_keyed_locators(tmp_path: Path) -> None:
    data = {"channel": "general", "messages": [{"content": "hello"}, {"content": "there"}]}
    path = write(tmp_path, "export.json", json.dumps(data))
    records = import_file(path, "case-a")
    assert [r.source_locator for r in records] == ["messages[0]", "messages[1]"]
    assert [r.content for r in records] == ["hello", "there"]


def test_json_item_without_a_text_field_keeps_the_whole_object(tmp_path: Path) -> None:
    path = write(tmp_path, "event.json", json.dumps({"action": "login", "user": "alex"}))
    records = import_file(path, "case-a")
    assert len(records) == 1
    assert records[0].source_locator == "$"
    assert json.loads(records[0].content) == {"action": "login", "user": "alex"}


def test_json_already_shaped_records_pass_through(tmp_path: Path) -> None:
    records = import_file(REPO_ROOT / "fixtures" / "synthetic_records.json", "synthetic-demo")
    assert [r.record_id for r in records] == ["rec-01", "rec-02"]
    assert records[0].source_file == "mailbox.eml"
    assert records[0].source_locator == "message 1, line 12"
    for record in records:
        assert_matches_schema(record)


def test_json_records_from_another_case_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(EvidenceImportError, match="belongs to case"):
        import_file(REPO_ROOT / "fixtures" / "synthetic_records.json", "some-other-case")


def test_invalid_json_gives_a_clear_error(tmp_path: Path) -> None:
    path = write(tmp_path, "broken.json", '{"text": "unterminated')
    with pytest.raises(EvidenceImportError, match="not valid JSON"):
        import_file(path, "case-a")


# --- CSV -----------------------------------------------------------------------


def test_csv_rows_become_records(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "calls.csv",
        "Date,From,To,Message\n"
        "2026-04-10 09:15,alex,jordan,running late\n"
        ",,,\n"
        "2026-04-10 09:40,jordan,alex,\"ok, see you\"\n",
    )
    records = import_file(path, "case-a")
    assert [r.source_locator for r in records] == ["row 1", "row 3"]
    assert records[0].content == "running late"
    assert records[0].timestamp == "2026-04-10 09:15"
    assert records[0].metadata == {"From": "alex", "To": "jordan"}
    assert records[1].content == "ok, see you"
    for record in records:
        assert_matches_schema(record)


def test_csv_without_a_text_column_joins_the_cells(tmp_path: Path) -> None:
    path = write(tmp_path, "visits.csv", "url;visit_count\nexample.test;3\n")
    records = import_file(path, "case-a")
    assert records[0].content == "url: example.test; visit_count: 3"
    assert records[0].metadata == {"url": "example.test", "visit_count": "3"}


# --- identifiers, hashes, errors ----------------------------------------------


def test_record_ids_are_stable_and_unique(tmp_path: Path) -> None:
    path = write(tmp_path, "chat.json", json.dumps([{"text": "a"}, {"text": "b"}]))
    first = import_file(path, "case-a")
    second = import_file(path, "case-a")
    assert [r.record_id for r in first] == [r.record_id for r in second]
    assert len({r.record_id for r in first}) == 2
    other_case = import_file(path, "case-b")
    assert {r.record_id for r in first}.isdisjoint({r.record_id for r in other_case})


def test_every_record_carries_the_source_file_hash(tmp_path: Path) -> None:
    import hashlib

    path = write(tmp_path, "notes.txt", "one\n\ntwo\n")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert {r.original_sha256 for r in import_file(path, "case-a")} == {expected}


def test_unsupported_format_is_a_clear_error(tmp_path: Path) -> None:
    path = write(tmp_path, "scan.pdf", "%PDF-1.7")
    with pytest.raises(UnsupportedFormatError, match=r"Unsupported format \.pdf"):
        import_file(path, "case-a")


def test_empty_case_id_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "notes.txt", "one\n")
    with pytest.raises(EvidenceImportError, match="case_id"):
        import_file(path, "  ")


# --- folders -------------------------------------------------------------------


def test_folder_import_reports_failures_and_skips_answer_keys(tmp_path: Path) -> None:
    write(tmp_path, "chats/chat.json", json.dumps([{"text": "hello"}]))
    write(tmp_path, "notes.txt", "a note\n")
    write(tmp_path, "scan.pdf", "%PDF-1.7")
    write(tmp_path, "Answer_Key.txt", "the culprit is ...\n")
    write(tmp_path, ".hidden.txt", "ignore me\n")

    result = import_path(tmp_path, "case-a")

    assert sorted(r.source_file for r in result.records) == ["chats/chat.json", "notes.txt"]
    assert [p.source_file for p in result.errors] == ["scan.pdf"]
    assert "Unsupported format" in result.errors[0].reason
    assert sorted(p.source_file for p in result.skipped) == [".hidden.txt", "Answer_Key.txt"]
    assert all("culprit" not in r.content for r in result.records)


def test_folder_import_strict_raises_on_first_failure(tmp_path: Path) -> None:
    write(tmp_path, "scan.pdf", "%PDF-1.7")
    with pytest.raises(UnsupportedFormatError):
        import_path(tmp_path, "case-a", strict=True)


def test_missing_path_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(EvidenceImportError, match="does not exist"):
        import_path(tmp_path / "nope", "case-a")


# --- command line ---------------------------------------------------------------


def test_cli_writes_records_and_fails_loudly_on_bad_files(tmp_path: Path) -> None:
    source = tmp_path / "exports"
    write(source, "chat.json", json.dumps([{"text": "hello"}]))
    out = tmp_path / "out" / "records.json"

    assert cli_main([str(source), "--case-id", "case-a", "--out", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert [item["content"] for item in written] == ["hello"]
    assert written[0]["case_id"] == "case-a"

    write(source, "scan.pdf", "%PDF-1.7")
    assert cli_main([str(source), "--case-id", "case-a", "--out", str(out)]) == 1
