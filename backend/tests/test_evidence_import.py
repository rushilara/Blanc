"""Tests for the evidence importer (BLA-9). All inputs are synthetic."""

import hashlib
import json
import shutil
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
FIXTURES = REPO_ROOT / "fixtures"
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
    records = import_file(path, "case-a", source_root=tmp_path)
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
    records = import_file(path, "case-a", source_root=tmp_path, txt_mode="line")
    assert [r.source_locator for r in records] == ["line 1", "line 3", "line 4"]
    assert records[0].timestamp == "2026-04-10 09:15:00"
    assert records[1].timestamp == "2026-04-10T09:20:00Z"
    assert records[2].timestamp is None


def test_txt_file_mode_is_one_record(tmp_path: Path) -> None:
    path = write(tmp_path, "letter.txt", "Dear A,\n\nSee you Friday.\n")
    records = import_file(path, "case-a", source_root=tmp_path, txt_mode="file")
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
    records = import_file(path, "case-a", source_root=tmp_path)
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
    records = import_file(path, "case-a", source_root=tmp_path)
    assert [r.source_locator for r in records] == ["messages[0]", "messages[1]"]
    assert [r.content for r in records] == ["hello", "there"]


def test_json_item_without_a_text_field_keeps_the_whole_object(tmp_path: Path) -> None:
    path = write(tmp_path, "event.json", json.dumps({"action": "login", "user": "alex"}))
    records = import_file(path, "case-a", source_root=tmp_path)
    assert len(records) == 1
    assert records[0].source_locator == "$"
    assert json.loads(records[0].content) == {"action": "login", "user": "alex"}


def test_json_already_shaped_records_pass_through() -> None:
    path = FIXTURES / "synthetic_records.json"
    records = import_file(path, "synthetic-demo", source_root=FIXTURES)
    assert [r.record_id for r in records] == ["rec-01", "rec-02"]
    assert records[0].source_file == "mailbox.eml"
    assert records[0].source_locator == "message 1, line 12"
    for record in records:
        assert_matches_schema(record)


def test_json_records_from_another_case_are_rejected() -> None:
    with pytest.raises(EvidenceImportError, match="belongs to case"):
        import_file(FIXTURES / "synthetic_records.json", "some-other-case", source_root=FIXTURES)


def test_invalid_json_gives_a_clear_error(tmp_path: Path) -> None:
    path = write(tmp_path, "broken.json", '{"text": "unterminated')
    with pytest.raises(EvidenceImportError, match="not valid JSON"):
        import_file(path, "case-a", source_root=tmp_path)


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
    records = import_file(path, "case-a", source_root=tmp_path)
    assert [r.source_locator for r in records] == ["line 2", "line 4"]
    assert records[0].content == "running late"
    assert records[0].timestamp == "2026-04-10 09:15"
    assert records[0].metadata == {"From": "alex", "To": "jordan"}
    assert records[1].content == "ok, see you"
    for record in records:
        assert_matches_schema(record)


def test_csv_locators_count_blank_lines(tmp_path: Path) -> None:
    # The reader silently skips the empty line; the locator must still point at line 4.
    path = write(tmp_path, "msgs.csv", "msg\na\n\nb\n")
    records = import_file(path, "case-a", source_root=tmp_path)
    assert [(r.content, r.source_locator) for r in records] == [("a", "line 2"), ("b", "line 4")]


def test_csv_row_with_a_multiline_cell_gets_a_line_range(tmp_path: Path) -> None:
    path = write(tmp_path, "msgs.csv", 'who,msg\nalex,"first\nsecond"\njordan,ok\n')
    records = import_file(path, "case-a", source_root=tmp_path)
    assert [r.source_locator for r in records] == ["lines 2-3", "line 4"]
    assert records[0].content == "first\nsecond"


def test_csv_without_a_text_column_joins_the_cells(tmp_path: Path) -> None:
    path = write(tmp_path, "visits.csv", "url;visit_count\nexample.test;3\n")
    records = import_file(path, "case-a", source_root=tmp_path)
    assert records[0].content == "url: example.test; visit_count: 3"
    assert records[0].metadata == {"url": "example.test", "visit_count": "3"}


# --- identifiers and hashes ------------------------------------------------------


def test_record_ids_are_stable_and_unique(tmp_path: Path) -> None:
    path = write(tmp_path, "chat.json", json.dumps([{"text": "a"}, {"text": "b"}]))
    first = import_file(path, "case-a", source_root=tmp_path)
    second = import_file(path, "case-a", source_root=tmp_path)
    assert [r.record_id for r in first] == [r.record_id for r in second]
    assert len({r.record_id for r in first}) == 2
    assert all(r.record_id.startswith("rec_") for r in first)
    other_case = import_file(path, "case-b", source_root=tmp_path)
    assert {r.record_id for r in first}.isdisjoint({r.record_id for r in other_case})


def test_record_ids_do_not_depend_on_how_the_import_was_started(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    path = write(root, "chats/chat.json", json.dumps([{"text": "a"}, {"text": "b"}]))

    whole_export = import_path(root, "case-a").records
    one_file = import_file(path, "case-a", source_root=root)
    one_file_via_path = import_path(path, "case-a", source_root=root).records
    subfolder = import_path(root / "chats", "case-a", source_root=root).records

    expected = [r.record_id for r in whole_export]
    assert len(expected) == 2
    for records in (one_file, one_file_via_path, subfolder):
        assert [r.record_id for r in records] == expected
        assert {r.source_file for r in records} == {"chats/chat.json"}


def test_a_single_file_needs_a_source_root(tmp_path: Path) -> None:
    path = write(tmp_path, "notes.txt", "one\n")
    with pytest.raises(EvidenceImportError, match="needs source_root"):
        import_file(path, "case-a")
    with pytest.raises(EvidenceImportError, match="needs source_root"):
        import_path(path, "case-a")


def test_a_path_outside_the_source_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    outside = write(tmp_path, "elsewhere/notes.txt", "one\n")
    with pytest.raises(EvidenceImportError, match="not inside source_root"):
        import_file(outside, "case-a", source_root=root)


def test_every_record_carries_the_source_file_hash(tmp_path: Path) -> None:
    path = write(tmp_path, "notes.txt", "one\n\ntwo\n")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    records = import_file(path, "case-a", source_root=tmp_path)
    assert {r.original_sha256 for r in records} == {expected}


# --- errors ----------------------------------------------------------------------


def test_unsupported_format_is_a_clear_error_for_a_single_file(tmp_path: Path) -> None:
    path = write(tmp_path, "scan.pdf", "%PDF-1.7")
    with pytest.raises(UnsupportedFormatError, match=r"Unsupported format \.pdf"):
        import_file(path, "case-a", source_root=tmp_path)
    # Passed directly to import_path it is still an error, never a silent skip.
    result = import_path(path, "case-a", source_root=tmp_path)
    assert result.records == [] and result.skipped == []
    assert [p.source_file for p in result.errors] == ["scan.pdf"]


def test_empty_case_id_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "notes.txt", "one\n")
    with pytest.raises(EvidenceImportError, match="case_id"):
        import_file(path, "  ", source_root=tmp_path)


def test_missing_path_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(EvidenceImportError, match="does not exist"):
        import_path(tmp_path / "nope", "case-a")


# --- folders -------------------------------------------------------------------


def test_folder_import_keeps_good_files_and_reports_the_rest(tmp_path: Path) -> None:
    write(tmp_path, "chats/chat.json", json.dumps([{"text": "hello"}]))
    write(tmp_path, "notes.txt", "a note\n")
    write(tmp_path, "broken.json", '{"text": "unterminated')
    write(tmp_path, "scan.pdf", "%PDF-1.7")
    write(tmp_path, "photo", "no extension")

    result = import_path(tmp_path, "case-a")

    assert sorted(r.source_file for r in result.records) == ["chats/chat.json", "notes.txt"]
    assert [p.source_file for p in result.errors] == ["broken.json"]
    assert "not valid JSON" in result.errors[0].reason
    # Formats the importer does not handle are skipped in a folder, not failures.
    assert {p.source_file: p.reason for p in result.skipped} == {
        "photo": "unsupported format (no extension)",
        "scan.pdf": "unsupported format .pdf",
    }


def test_answer_keys_are_skipped_by_file_name_and_by_folder_name(tmp_path: Path) -> None:
    write(tmp_path, "notes.txt", "a note\n")
    write(tmp_path, "Answer_Key.txt", "the culprit is X\n")
    write(tmp_path, "answer_keys/q1.txt", "the culprit is X\n")
    write(tmp_path, "solutions/day1.json", json.dumps([{"text": "the culprit is X"}]))
    write(tmp_path, "docs/Teacher Guide/notes.txt", "the culprit is X\n")

    result = import_path(tmp_path, "case-a")

    assert [r.source_file for r in result.records] == ["notes.txt"]
    assert all("culprit" not in r.content for r in result.records)
    assert sorted(p.source_file for p in result.skipped) == [
        "Answer_Key.txt",
        "answer_keys/q1.txt",
        "docs/Teacher Guide/notes.txt",
        "solutions/day1.json",
    ]
    assert result.errors == []


def test_hidden_files_and_folders_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, "notes.txt", "a note\n")
    write(tmp_path, ".hidden.txt", "ignore me\n")
    write(tmp_path, ".git/x.json", json.dumps([{"text": "not evidence"}]))
    write(tmp_path, "chats/.cache/y.txt", "not evidence\n")

    result = import_path(tmp_path, "case-a")

    assert [r.source_file for r in result.records] == ["notes.txt"]
    assert sorted(p.source_file for p in result.skipped) == [
        ".git/x.json",
        ".hidden.txt",
        "chats/.cache/y.txt",
    ]
    assert {p.reason for p in result.skipped} == {"hidden file or folder"}


def test_a_duplicate_id_across_files_fails_only_the_second_file(tmp_path: Path) -> None:
    write(tmp_path, "notes.txt", "a note\n")
    shutil.copy(FIXTURES / "synthetic_records.json", tmp_path / "a_export.json")
    shutil.copy(FIXTURES / "synthetic_records.json", tmp_path / "b_copy_of_export.json")

    result = import_path(tmp_path, "synthetic-demo")

    # The first copy and the unrelated file are kept; only the repeat is rejected.
    assert sorted(r.record_id for r in result.records if r.record_id.startswith("rec-")) == [
        "rec-01",
        "rec-02",
    ]
    assert any(r.source_file == "notes.txt" for r in result.records)
    assert len(result.records) == 3
    assert [p.source_file for p in result.errors] == ["b_copy_of_export.json"]
    assert "repeats record_id rec-01" in result.errors[0].reason

    with pytest.raises(EvidenceImportError, match="repeats record_id"):
        import_path(tmp_path, "synthetic-demo", strict=True)


def test_folder_import_strict_raises_on_first_failure(tmp_path: Path) -> None:
    write(tmp_path, "broken.json", '{"text": "unterminated')
    with pytest.raises(EvidenceImportError, match="not valid JSON"):
        import_path(tmp_path, "case-a", strict=True)


# --- command line ---------------------------------------------------------------


def test_cli_writes_records_and_fails_loudly_on_bad_files(tmp_path: Path) -> None:
    source = tmp_path / "exports"
    write(source, "chat.json", json.dumps([{"text": "hello"}]))
    write(source, "scan.pdf", "%PDF-1.7")
    out = tmp_path / "out" / "records.json"

    # An unsupported file in a folder is skipped, so a real export folder still exits 0.
    assert cli_main([str(source), "--case-id", "case-a", "--out", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert [item["content"] for item in written] == ["hello"]
    assert written[0]["case_id"] == "case-a"

    write(source, "broken.json", '{"text": "unterminated')
    assert cli_main([str(source), "--case-id", "case-a", "--out", str(out)]) == 1


def test_cli_single_file_needs_and_uses_the_source_root(tmp_path: Path) -> None:
    source = tmp_path / "exports"
    path = write(source, "chats/chat.json", json.dumps([{"text": "hello"}]))
    out = tmp_path / "records.json"

    assert cli_main([str(path), "--case-id", "case-a", "--out", str(out)]) == 1
    assert not out.exists()

    args = [str(path), "--case-id", "case-a", "--source-root", str(source), "--out", str(out)]
    assert cli_main(args) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written[0]["source_file"] == "chats/chat.json"
    assert written[0]["record_id"] == import_path(source, "case-a").records[0].record_id
