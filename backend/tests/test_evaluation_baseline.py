"""Tests for the keyword-only retrieval baseline (BLA-51). All inputs are synthetic."""

import copy
import json
from pathlib import Path

import pytest
from _paths import FIXTURES_DIR, RAW

from app.contracts import EvidenceRecord
from app.evaluation import FileResults, QuestionSet
from app.evaluation.baseline import (
    compare_retrieval,
    format_comparison,
    keyword_only_checks,
    keyword_rank,
    tokenize,
)
from app.evaluation.runner import load_question_set, main

QUESTION_SET_PATH = Path(__file__).parent / "examples" / "synthetic_question_set.json"
QID = "q1-connection"


def record(record_id: str, content: str, case_id: str = "synthetic-demo") -> EvidenceRecord:
    return EvidenceRecord(
        record_id=record_id,
        case_id=case_id,
        source_file="notes.txt",
        source_locator=f"line {record_id}",
        content=content,
    )


@pytest.fixture
def question_set() -> QuestionSet:
    return load_question_set(QUESTION_SET_PATH)


@pytest.fixture
def records() -> list[EvidenceRecord]:
    return [EvidenceRecord.model_validate(item) for item in copy.deepcopy(RAW["records"])]


def save_base(folder: Path, base: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{QID}.json").write_text(json.dumps(base), encoding="utf-8")
    return folder


def test_tokenize_lowercases_and_drops_stop_words() -> None:
    assert tokenize("How are Alex and Casey connected?") == ["alex", "casey", "connected"]
    assert tokenize("The, the; THE.") == []


def test_keyword_rank_returns_matching_records_best_first() -> None:
    corpus = [
        record("r1", "Alex emails Jordan about delivering a document on Friday."),
        record("r2", "Jordan tells Casey there may be a meeting."),
        record("r3", "The weather was fine all week."),
        record("r4", "Casey, Casey and Casey again: Casey confirmed with Alex."),
    ]
    ranked = keyword_rank("How are Alex and Casey connected?", corpus)
    assert ranked[0] == "r4"  # mentions both names
    assert set(ranked) == {"r1", "r2", "r4"}  # r3 shares no keyword, so it is never returned
    assert keyword_rank("How are Alex and Casey connected?", corpus, k=1) == ["r4"]


def test_keyword_rank_is_stable_and_handles_empty_input() -> None:
    corpus = [record("b", "same words here"), record("a", "same words here")]
    assert keyword_rank("same words", corpus) == ["a", "b"]  # tie broken by record ID
    assert keyword_rank("", corpus) == []
    assert keyword_rank("the of and", corpus) == []  # only stop words
    assert keyword_rank("anything", []) == []


def test_keyword_search_misses_a_paraphrase_the_pipeline_found(
    question_set, records, tmp_path
) -> None:
    # The question and the record describe the same thing in different words.
    question = question_set.questions[0]
    question.question = "Who arranged the handover of the paperwork?"
    question.expected_record_ids = ["rec-01"]
    base = copy.deepcopy(RAW["revision"])  # the saved pipeline result retrieved rec-01
    results = FileResults(save_base(tmp_path / "results", base))

    (row,) = compare_retrieval(question_set, records, results)
    assert (row.keyword_hits, row.pipeline_hits, row.expected) == (0, 1, 1)
    assert row.keyword_missing == ["rec-01"] and row.pipeline_missing == []

    table = format_comparison([row])
    assert "q1-connection         1      0/1       1/1" in table
    assert "TOTAL                 1       0%      100%" in table
    assert "q1-connection: found by the pipeline but not by keyword search: rec-01" in table


def test_when_both_find_everything_the_table_says_so(question_set, records, tmp_path) -> None:
    results = FileResults(save_base(tmp_path / "results", copy.deepcopy(RAW["revision"])))
    rows = compare_retrieval(question_set, records, results)
    assert [(r.keyword_hits, r.pipeline_hits) for r in rows] == [(2, 2)]
    table = format_comparison(rows)
    assert "TOTAL                 2     100%      100%" in table
    assert "found by" not in table  # nothing that only one method found


def test_top_k_limits_both_sides(question_set, records, tmp_path) -> None:
    results = FileResults(save_base(tmp_path / "results", copy.deepcopy(RAW["revision"])))
    (row,) = compare_retrieval(question_set, records, results, k=1)
    assert row.keyword_hits == 1 and row.pipeline_hits == 1
    assert "Retrieval, top 1:" in format_comparison([row], 1)


def test_a_record_only_keyword_search_finds_is_reported(question_set, records, tmp_path) -> None:
    base = copy.deepcopy(RAW["revision"])
    base["retrieved_record_ids"] = ["rec-01"]
    for key in ("claims", "timeline_events"):
        base[key] = [item for item in base[key] if item["evidence_ids"] == ["rec-01"]]
    base["graph"]["edges"] = [e for e in base["graph"]["edges"] if e["evidence_ids"] == ["rec-01"]]
    results = FileResults(save_base(tmp_path / "results", base))
    (row,) = compare_retrieval(question_set, records, results)
    assert (row.keyword_hits, row.pipeline_hits) == (2, 1)
    assert "found by keyword search but not by the pipeline: rec-02" in format_comparison([row])


def test_a_missing_pipeline_result_is_shown_not_counted(question_set, records, tmp_path) -> None:
    (tmp_path / "empty").mkdir()
    (row,) = compare_retrieval(question_set, records, FileResults(tmp_path / "empty"))
    assert row.pipeline_hits is None
    table = format_comparison([row])
    assert "no result" in table
    assert table.splitlines()[4].endswith("n/a")


def test_only_the_question_sets_case_is_searched(question_set, records) -> None:
    # Another case reuses the record ID rec-01 for a record that matches the question.
    question = question_set.questions[0]
    question.question = "Who arranged the handover of the paperwork?"
    question.expected_record_ids = ["rec-01"]
    other = record("rec-01", "Bram arranged the handover of the paperwork.", case_id="other-case")
    assert keyword_rank(question.question, [*records, other]) == ["rec-01"]  # unscoped
    (row,) = compare_retrieval(question_set, [*records, other])
    assert row.keyword_hits == 0 and row.keyword_missing == ["rec-01"]
    # The other case's records do not change the scores either.
    assert compare_retrieval(question_set, [*records, other], k=1) == compare_retrieval(
        question_set, records, k=1
    )


@pytest.mark.parametrize(
    ("change", "problem"),
    [({"case_id": "other-case"}, "wrong case"), ({"analysis_id": ""}, "invalid result")],
)
def test_an_unusable_pipeline_result_is_named_not_counted(
    question_set, records, tmp_path, change, problem
) -> None:
    base = {**copy.deepcopy(RAW["revision"]), **change}
    (row,) = compare_retrieval(question_set, records, FileResults(save_base(tmp_path, base)))
    assert row.pipeline_hits is None and row.pipeline_problem == problem
    table = format_comparison([row])
    assert problem in table.splitlines()[3]
    assert table.splitlines()[4].endswith("n/a")


def test_the_table_says_the_keyword_side_is_not_the_pipelines_retriever(
    question_set, records
) -> None:
    table = format_comparison(compare_retrieval(question_set, records))
    assert table.splitlines()[1] == (
        "(keyword: plain BM25, no stemming; not the pipeline's own keyword retriever)"
    )


def test_questions_without_expected_records_are_left_out(question_set, records) -> None:
    question_set.questions[0].expected_record_ids = []
    assert compare_retrieval(question_set, records) == []
    assert format_comparison([]) == "Retrieval comparison: no question lists expected records."


def test_keyword_only_checks_pass_and_fail_like_the_retrieval_check(question_set, records) -> None:
    (check,) = keyword_only_checks(question_set, records)
    assert check.passed and check.check == "retrieval.keyword"
    assert check.detail == "2/2 expected records in the keyword top 10"

    question_set.questions[0].question = "Who arranged the handover of the paperwork?"
    (check,) = keyword_only_checks(question_set, records)
    assert not check.passed
    assert check.detail == "0/2 expected records in the keyword top 10; missing rec-01, rec-02"


def cli_base(tmp_path: Path) -> list[str]:
    return [
        "--questions",
        str(QUESTION_SET_PATH),
        "--records",
        str(FIXTURES_DIR / "synthetic_records.json"),
    ]


def test_cli_keyword_baseline_adds_the_table_without_changing_the_result(
    tmp_path, capsys
) -> None:
    results = save_base(tmp_path / "results", copy.deepcopy(RAW["revision"]))
    (results / f"{QID}.excluded.json").write_text(json.dumps(RAW["child"]), encoding="utf-8")
    args = [*cli_base(tmp_path), "--results", str(results)]

    assert main(args) == 0
    plain = capsys.readouterr().out
    assert "Retrieval, top" not in plain

    assert main([*args, "--keyword-baseline"]) == 0
    with_table = capsys.readouterr().out
    assert with_table.startswith(plain)
    assert "Retrieval, top 10: keyword-only search next to the pipeline" in with_table
    assert "TOTAL                 2     100%      100%" in with_table


def test_cli_keyword_only_needs_no_results_folder(tmp_path, capsys) -> None:
    assert main([*cli_base(tmp_path), "--keyword-only"]) == 0
    output = capsys.readouterr().out
    assert "PASS  q1-connection  retrieval.keyword: 2/2" in output
    assert output.strip().endswith("OK: 1/1 questions passed (1/1 checks)")

    assert main([*cli_base(tmp_path), "--keyword-only", "--top-k", "1"]) == 1
    assert "1/2 expected records in the keyword top 1" in capsys.readouterr().out


def test_cli_still_requires_results_for_a_full_run(tmp_path, capsys) -> None:
    assert main(cli_base(tmp_path)) == 2
    assert "--results is required unless --keyword-only is given" in capsys.readouterr().err


def test_cli_rejects_both_keyword_flags(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main([*cli_base(tmp_path), "--keyword-only", "--keyword-baseline"])
    assert raised.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
