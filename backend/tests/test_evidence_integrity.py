"""Tests for the corpus integrity check (BLA-11). All inputs are synthetic."""

import json
from pathlib import Path

import pytest

from app.evidence import import_path
from app.evidence.integrity import check_corpus, load_records
from app.evidence.integrity import main as integrity_main


def make_corpus(tmp_path: Path) -> tuple[Path, list[dict]]:
    """A small imported case: a source folder and its records, as the importer writes them."""
    source = tmp_path / "exports"
    (source / "chats").mkdir(parents=True)
    (source / "chats" / "chat.json").write_text(
        json.dumps([{"text": "hello"}, {"text": "see you friday"}]), encoding="utf-8"
    )
    (source / "notes.txt").write_text("a note\n", encoding="utf-8")
    result = import_path(source, "case-a", strict=True)
    return source, [record.model_dump() for record in result.records]


def kinds(issues: list) -> list[str]:
    return sorted(issue.kind for issue in issues)


def write_records(tmp_path: Path, records: list) -> Path:
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def test_a_clean_import_has_no_problems(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    assert len(records) == 3
    assert check_corpus(records, case_id="case-a", source_root=source) == []


def test_duplicate_record_ids_are_reported_once_with_a_count(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    issues = check_corpus(
        [*records, records[0], records[0]], case_id="case-a", source_root=source
    )
    assert kinds(issues) == ["duplicate_record_id"]
    assert issues[0].record_id == records[0]["record_id"]
    assert "appears 3 times" in issues[0].detail


def test_missing_source_file_is_reported_per_file(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    (source / "chats" / "chat.json").unlink()
    issues = check_corpus(records, case_id="case-a", source_root=source)
    assert kinds(issues) == ["missing_source_file"]
    assert issues[0].source_file == "chats/chat.json"
    assert "2 records" in issues[0].detail


def test_a_source_path_outside_the_root_counts_as_missing(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    (tmp_path / "outside.txt").write_text("not part of the case\n", encoding="utf-8")
    stray = {**records[0], "record_id": "rec-stray", "source_file": "../outside.txt"}
    issues = check_corpus([*records, stray], case_id="case-a", source_root=source)
    assert kinds(issues) == ["missing_source_file"]
    assert issues[0].record_id == "rec-stray"


def test_wrong_case_id_is_reported(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    records[1] = {**records[1], "case_id": "case-b"}
    issues = check_corpus(records, case_id="case-a", source_root=source)
    assert kinds(issues) == ["case_mismatch"]
    assert issues[0].record_id == records[1]["record_id"]
    assert "'case-b'" in issues[0].detail


def test_a_source_file_edited_after_import_is_reported(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    (source / "notes.txt").write_text("a note, quietly changed\n", encoding="utf-8")
    issues = check_corpus(records, case_id="case-a", source_root=source)
    assert kinds(issues) == ["source_changed"]
    assert issues[0].source_file == "notes.txt"


def test_invalid_records_are_reported_without_stopping_the_check(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    broken = {key: value for key, value in records[0].items() if key != "source_locator"}
    broken["record_id"] = "rec-broken"
    issues = check_corpus([*records, broken, "not an object"], case_id="case-a", source_root=source)
    assert kinds(issues) == ["invalid_record", "invalid_record"]
    assert any("source_locator" in issue.detail for issue in issues)
    assert any("index 4 is not an object" in issue.detail for issue in issues)


def test_records_from_an_answer_key_are_flagged(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    leaked = {**records[0], "record_id": "rec-leak", "source_file": "Teacher_Guide_v2.txt"}
    issues = check_corpus([*records, leaked], case_id="case-a", source_root=None)
    assert kinds(issues) == ["restricted_source"]
    assert issues[0].source_file == "Teacher_Guide_v2.txt"


def test_without_a_source_root_only_record_checks_run(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    (source / "notes.txt").unlink()
    assert check_corpus(records, case_id="case-a", source_root=None) == []


def test_load_records_rejects_a_non_array_file(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(json.dumps({"records": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        load_records(path)


def test_cli_exits_zero_when_clean_and_nonzero_on_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, records = make_corpus(tmp_path)
    path = write_records(tmp_path, records)
    args = [str(path), "--case-id", "case-a", "--source-root", str(source)]

    assert integrity_main(args) == 0
    assert "OK: 3 records" in capsys.readouterr().out

    write_records(tmp_path, [*records, records[0]])
    assert integrity_main(args) == 1
    output = capsys.readouterr().out
    assert "duplicate_record_id" in output
    assert "FAILED: 1 problems in 4 records" in output


def test_cli_exits_two_when_it_cannot_run(tmp_path: Path) -> None:
    source, records = make_corpus(tmp_path)
    missing = tmp_path / "nope.json"
    assert integrity_main([str(missing), "--case-id", "case-a", "--skip-source-check"]) == 2
    path = write_records(tmp_path, records)
    not_a_folder = tmp_path / "no-such-folder"
    assert (
        integrity_main([str(path), "--case-id", "case-a", "--source-root", str(not_a_folder)])
        == 2
    )


def test_cli_says_when_source_files_were_not_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, records = make_corpus(tmp_path)
    path = write_records(tmp_path, records)
    assert integrity_main([str(path), "--case-id", "case-a", "--skip-source-check"]) == 0
    assert "source files were not checked" in capsys.readouterr().out
