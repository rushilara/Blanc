"""Evaluation runner: check pipeline output against a human-verified question set.

Run from `backend/`:

    python -m app.evaluation --questions questions.json --records records.json --results results/

`results/` holds one analysis revision per question, named `<question_id>.json`,
and, for questions with an exclusion, the child revision produced after
excluding the pivotal records, named `<question_id>.excluded.json`. Reading
saved revisions keeps the runner independent of how they were produced: by hand
today, by the analyze and exclude endpoints later (see `ResultSource`).

Checks, per question:
- result: the revision exists, matches the contract and belongs to the case;
- retrieval: every expected record was retrieved (within `--top-k` if given);
- grounding: cross-reference validation passes, with the share of findings
  that cite valid, permitted records;
- findings: every expected claim, edge or event is present;
- unresolved: an unanswerable question is reported as unresolved, and no claim
  is asserted as observed, reported or inferred;
- safety: forbidden text (an injected instruction's payload) is absent;
- exclusion.applied: every record excluded so far, the parent's exclusions plus
  the new ones, is gone from the child's retrieval and citations;
- exclusion.effect: each affected finding weakens or loses support as expected;
- exclusion.safety: forbidden text is absent from the child revision too.

Exits 0 when every check passes, 1 when any fails, 2 when it cannot run.

Keyword baseline (BLA-51, see `app.evaluation.baseline`): `--keyword-baseline` adds
a table of top-k hit rates for keyword-only search next to the pipeline's, without
changing the exit code. `--keyword-only` needs no `--results`: it runs just the
retrieval check with keyword search in place of the pipeline.

Not covered: whether an excerpt shown in the UI matches its record. Revisions
cite record IDs, not excerpts, so that belongs to the citation audit (BLA-46).
"""

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from app.contracts import AnalysisRevision, EvidenceRecord
from app.evaluation.questions import ExpectedChange, FindingKind, Question, QuestionSet
from app.validation import format_validation_error, schema_errors, validate_revision


@dataclass(frozen=True)
class CheckResult:
    question_id: str
    check: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return f"{verdict}  {self.question_id}  {self.check}: {self.detail}"


class ResultSource(Protocol):
    """Where the runner gets the pipeline's output for a question, as raw JSON."""

    def base(self, question_id: str) -> Any | None: ...

    def excluded(self, question_id: str) -> Any | None: ...


class FileResults:
    """Revisions saved as `<question_id>.json` and `<question_id>.excluded.json`."""

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)

    def base(self, question_id: str) -> Any | None:
        return self._read(f"{question_id}.json")

    def excluded(self, question_id: str) -> Any | None:
        return self._read(f"{question_id}.excluded.json")

    def _read(self, name: str) -> Any | None:
        path = self.folder / name
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def evaluate(
    question_set: QuestionSet,
    records: Sequence[EvidenceRecord],
    results: ResultSource,
    *,
    top_k: int | None = None,
) -> list[CheckResult]:
    """Run every question and return every check, in question order."""
    checks: list[CheckResult] = []
    for question in question_set.questions:
        checks.extend(
            evaluate_question(
                question,
                records,
                results.base(question.question_id),
                results.excluded(question.question_id),
                case_id=question_set.case_id,
                top_k=top_k,
            )
        )
    return checks


def evaluate_question(
    question: Question,
    records: Sequence[EvidenceRecord],
    base_raw: Any | None,
    excluded_raw: Any | None = None,
    *,
    case_id: str,
    top_k: int | None = None,
) -> list[CheckResult]:
    qid = question.question_id
    checks: list[CheckResult] = []

    def add(check: str, passed: bool, detail: str) -> None:
        checks.append(CheckResult(qid, check, passed, detail))

    base = _load_revision(base_raw, case_id, "result", add)
    if base is None:
        return checks

    if question.expected_record_ids:
        retrieved = base.retrieved_record_ids
        if top_k is not None:
            retrieved = retrieved[:top_k]
        missing = [rid for rid in question.expected_record_ids if rid not in retrieved]
        total = len(question.expected_record_ids)
        scope = "" if top_k is None else f" in the top {top_k}"
        detail = f"{total - len(missing)}/{total} expected records retrieved{scope}"
        if missing:
            detail += f"; missing {', '.join(missing)}"
        add("retrieval", not missing, detail)

    add("grounding", *_grounding(records, base))

    if question.expected_findings:
        absent = [
            f"{finding.kind} citing {', '.join(finding.evidence_ids)}"
            for finding in question.expected_findings
            if not _matching(base, finding.kind, finding.evidence_ids)
        ]
        total = len(question.expected_findings)
        detail = f"{total - len(absent)}/{total} expected findings present"
        if absent:
            detail += f"; missing {'; '.join(absent)}"
        add("findings", not absent, detail)

    if question.expect_unresolved:
        count = len(base.unresolved_questions)
        asserted = [claim.id for claim in base.claims if claim.status != "unresolved"]
        if not count:
            add("unresolved", False, "the analysis reports nothing as unresolved")
        elif asserted:
            add(
                "unresolved",
                False,
                f"{count} unresolved questions reported, but it still asserts "
                f"{', '.join(asserted)}",
            )
        else:
            add("unresolved", True, f"{count} unresolved questions reported, no claim asserted")

    if question.must_not_contain:
        add("safety", *_safety(question, base))

    if question.exclusion is not None:
        child = _load_revision(excluded_raw, case_id, "exclusion.result", add)
        if child is not None:
            add("exclusion.applied", *_exclusion_applied(question, records, base, child))
            for change in question.exclusion.expect:
                add("exclusion.effect", *_exclusion_effect(change, base, child))
            if question.must_not_contain:
                # The child is model output too, written after reading the remaining records.
                add("exclusion.safety", *_safety(question, child))

    return checks


def _load_revision(raw: Any | None, case_id: str, check: str, add: Any) -> AnalysisRevision | None:
    if raw is None:
        add(check, False, "no saved revision found")
        return None
    revision, errors = schema_errors(AnalysisRevision, raw)
    if revision is None:
        add(check, False, f"does not match the contract: {errors[0]}")
        return None
    if revision.case_id != case_id:
        add(check, False, f"belongs to case {revision.case_id!r}, expected {case_id!r}")
        return None
    add(check, True, f"revision {revision.analysis_id} loaded")
    return revision


def _items(revision: AnalysisRevision, kind: FindingKind) -> list[Any]:
    if kind == "claim":
        return list(revision.claims)
    if kind == "edge":
        return list(revision.graph.edges)
    return list(revision.timeline_events)


def _matching(
    revision: AnalysisRevision, kind: FindingKind, evidence_ids: Iterable[str]
) -> list[Any]:
    """Findings of this kind that cite every one of the given records."""
    wanted = set(evidence_ids)
    return [item for item in _items(revision, kind) if wanted <= set(item.evidence_ids)]


def _grounding(records: Sequence[EvidenceRecord], revision: AnalysisRevision) -> tuple[bool, str]:
    errors = validate_revision(records, revision)
    known = {record.record_id for record in records if record.case_id == revision.case_id}
    permitted = (known & set(revision.retrieved_record_ids)) - set(revision.excluded_record_ids)
    findings = [item for kind in ("claim", "edge", "event") for item in _items(revision, kind)]
    grounded = sum(
        1 for item in findings if item.evidence_ids and set(item.evidence_ids) <= permitted
    )
    detail = f"{grounded}/{len(findings)} findings cite valid, permitted records"
    if errors:
        detail += f"; {errors[0]}"
        if len(errors) > 1:
            detail += f" (+{len(errors) - 1} more)"
    return not errors and grounded == len(findings), detail


def _safety(question: Question, revision: AnalysisRevision) -> tuple[bool, str]:
    text = _all_text(revision).lower()
    found = [needle for needle in question.must_not_contain if needle.lower() in text]
    if found:
        return False, f"forbidden text present: {', '.join(repr(item) for item in found)}"
    return True, f"none of {len(question.must_not_contain)} forbidden strings appear"


def _all_text(revision: AnalysisRevision) -> str:
    parts = [revision.answer, *revision.unresolved_questions]
    parts += [claim.text for claim in revision.claims]
    parts += [edge.description or "" for edge in revision.graph.edges]
    parts += [node.label for node in revision.graph.nodes]
    parts += [event.description for event in revision.timeline_events]
    return "\n".join(parts)


def _exclusion_applied(
    question: Question,
    records: Sequence[EvidenceRecord],
    base: AnalysisRevision,
    child: AnalysisRevision,
) -> tuple[bool, str]:
    """The child must keep the parent's exclusions and add the requested ones.

    An exclusion never restores evidence: a record excluded in the parent stays
    excluded in the child. Restoring a record is a separate operation and is not
    what this check evaluates.
    """
    assert question.exclusion is not None
    requested = question.exclusion.exclude_record_ids
    inherited = [rid for rid in base.excluded_record_ids if rid not in requested]
    effective = [*inherited, *requested]
    problems: list[str] = []

    not_excluded = [rid for rid in requested if rid not in child.excluded_record_ids]
    if not_excluded:
        problems.append(f"not marked excluded: {', '.join(not_excluded)}")
    restored = [rid for rid in inherited if rid not in child.excluded_record_ids]
    if restored:
        problems.append(f"previously excluded records restored: {', '.join(restored)}")
    still_retrieved = [rid for rid in effective if rid in child.retrieved_record_ids]
    if still_retrieved:
        problems.append(f"still retrieved: {', '.join(still_retrieved)}")
    cited = {
        rid
        for kind in ("claim", "edge", "event")
        for item in _items(child, kind)
        for rid in item.evidence_ids
    }
    still_cited = [rid for rid in effective if rid in cited]
    if still_cited:
        problems.append(f"still cited: {', '.join(still_cited)}")
    if child.parent_analysis_id != base.analysis_id:
        problems.append(
            f"parent is {child.parent_analysis_id!r}, expected {base.analysis_id!r}"
        )
    errors = validate_revision(records, child)
    if errors:
        problems.append(errors[0])
    if problems:
        return False, "; ".join(problems)
    return True, f"{', '.join(effective)} excluded from retrieval and citations"


def _exclusion_effect(
    change: ExpectedChange, base: AnalysisRevision, child: AnalysisRevision
) -> tuple[bool, str]:
    label = f"{change.kind} citing {', '.join(change.evidence_ids)}"
    original = _closest(_matching(base, change.kind, change.evidence_ids), change.evidence_ids)
    if original is None:
        return False, f"{label} is not in the base revision, so its change cannot be judged"
    observed = _observed_status(original, _items(child, change.kind))
    allowed = ", ".join(change.allowed)
    return observed in change.allowed, f"{label} is {observed} (expected {allowed})"


def _closest(candidates: Sequence[Any], evidence_ids: Iterable[str]) -> Any | None:
    """The finding the answer key means: one citing exactly these records if there is
    one, otherwise the one citing the fewest extra records.

    A base revision often has a broad claim citing several records alongside narrower
    ones. "The claim citing rec-02" must mean the rec-02 claim, not a combined claim
    that happens to cite rec-02 as well and is listed first.
    """
    wanted = set(evidence_ids)
    best: Any | None = None
    for item in candidates:
        if set(item.evidence_ids) == wanted:
            return item
        if best is None or len(set(item.evidence_ids)) < len(set(best.evidence_ids)):
            best = item
    return best


def _observed_status(original: Any, child_items: list[Any]) -> str:
    """How support for one base finding changed, judged only by the records cited.

    Its counterpart in the child is the finding citing exactly the same records; failing
    that, one citing only a subset of them; failing that, the one sharing the most. The
    last case is a guess, since one record can support unrelated findings: see the
    limitation noted in `app.evaluation.questions`.
    """
    before = set(original.evidence_ids)
    survivors = [item for item in child_items if before & set(item.evidence_ids)]
    if not survivors:
        return "not_supported"

    def closeness(item: Any) -> tuple[int, int, int]:
        cited = set(item.evidence_ids)
        return (cited == before, cited <= before, len(cited & before))

    best = max(survivors, key=closeness)
    if getattr(best, "status", None) == "unresolved":
        return "unresolved"
    if len(before & set(best.evidence_ids)) < len(before):
        return "weakened"
    return "still_supported"


# --- command line -----------------------------------------------------------------


def _load_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"could not read {what} {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{what} {path} is not valid JSON: {error.msg}") from error


def load_question_set(path: str | Path) -> QuestionSet:
    try:
        return QuestionSet.model_validate(_load_json(Path(path), "question set"))
    except ValidationError as error:
        raise ValueError(f"invalid question set: {format_validation_error(error)[0]}") from error


def load_records(path: str | Path) -> list[EvidenceRecord]:
    data = _load_json(Path(path), "records file")
    if not isinstance(data, list):
        raise ValueError(f"records file {path} must contain a JSON array")
    records = []
    for index, raw in enumerate(data):
        record, errors = schema_errors(EvidenceRecord, raw)
        if record is None:
            raise ValueError(f"record at index {index} is invalid: {errors[0]}")
        records.append(record)
    return records


def summarize(question_set: QuestionSet, checks: Sequence[CheckResult]) -> str:
    failed_questions = {check.question_id for check in checks if not check.passed}
    total = len(question_set.questions)
    passed_checks = sum(1 for check in checks if check.passed)
    verdict = "FAILED" if failed_questions else "OK"
    return (
        f"{verdict}: {total - len(failed_questions)}/{total} questions passed "
        f"({passed_checks}/{len(checks)} checks)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description="Check saved analysis revisions against a human-verified question set.",
    )
    parser.add_argument("--questions", type=Path, required=True, help="Question set JSON.")
    parser.add_argument("--records", type=Path, required=True, help="The case's records JSON.")
    parser.add_argument("--results", type=Path, help="Folder of saved revisions.")
    parser.add_argument("--top-k", type=int, help="Only count the first K retrieved records.")
    parser.add_argument(
        "--keyword-baseline",
        action="store_true",
        help="Also print keyword-only top-k hit rates next to the pipeline's.",
    )
    parser.add_argument(
        "--keyword-only",
        action="store_true",
        help="Run only the retrieval check, using keyword search; --results is not needed.",
    )
    parser.add_argument("--json", type=Path, dest="json_out", help="Also write results as JSON.")
    args = parser.parse_args(argv)

    # Imported here because the baseline builds on this module.
    from app.evaluation import baseline

    comparison = None
    try:
        question_set = load_question_set(args.questions)
        records = load_records(args.records)
        if args.top_k is not None and args.top_k < 1:
            raise ValueError("--top-k must be at least 1")
        k = args.top_k or baseline.DEFAULT_K
        if args.keyword_only:
            checks = baseline.keyword_only_checks(question_set, records, k=k)
        else:
            if args.results is None:
                raise ValueError("--results is required unless --keyword-only is given")
            if not args.results.is_dir():
                raise ValueError(f"results folder not found: {args.results}")
            results = FileResults(args.results)
            checks = evaluate(question_set, records, results, top_k=args.top_k)
            if args.keyword_baseline:
                rows = baseline.compare_retrieval(question_set, records, results, k=k)
                comparison = baseline.format_comparison(rows, k)
    except (ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    for check in checks:
        print(check)
    print(summarize(question_set, checks))
    if comparison is not None:
        print()
        print(comparison)
    if args.json_out is not None:
        payload = [
            {"question_id": c.question_id, "check": c.check, "passed": c.passed, "detail": c.detail}
            for c in checks
        ]
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if all(check.passed for check in checks) else 1
