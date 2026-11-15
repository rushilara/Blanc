"""The question-set format: a small, human-verified answer key for one case.

A question set is evaluation material, not evidence. Keep it outside any folder
that gets imported, so it can never reach the searchable corpus.

Findings are matched by the records they cite, not by wording or ID, because an
LLM words and numbers its claims differently on every run. "A claim citing
rec-07 and rec-12" is something a human can verify once and a script can check
every time.

Limitation: one record can support several unrelated findings, so matching by
citations is fuzzy when a finding has no counterpart citing the same records.
Keep the `evidence_ids` of expected findings and expected changes specific: name
exactly the records the finding rests on. The runner prefers a finding citing
exactly those records over a broader one that merely includes them.

`expect_unresolved` is for questions the evidence cannot answer. It requires the
analysis to list at least one unresolved question and to assert no claim as
observed, reported or inferred.

See `tests/examples/synthetic_question_set.json` for a complete example.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts import SupportStatus

FindingKind = Literal["claim", "edge", "event"]
QuestionKind = Literal["multi_record", "temporal", "unanswerable", "injection", "other"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedFinding(_Model):
    """A claim, edge or event that must appear, identified by the records it cites."""

    kind: FindingKind
    evidence_ids: list[str] = Field(min_length=1)
    note: str | None = None


class ExpectedChange(_Model):
    """What should happen to one finding once the pivotal records are excluded."""

    kind: FindingKind
    evidence_ids: list[str] = Field(min_length=1)
    allowed: list[SupportStatus] = Field(min_length=1)
    note: str | None = None


class Exclusion(_Model):
    exclude_record_ids: list[str] = Field(min_length=1)
    expect: list[ExpectedChange] = Field(default_factory=list)


class SourceLocator(_Model):
    """Where an expected record came from, so a human can find it in the original export
    and the runner can tell when a re-import gave the record ID to different evidence."""

    source_file: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)


class Question(_Model):
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    kind: QuestionKind = "other"
    # Records a correct answer has to draw on; checked against what was retrieved.
    expected_record_ids: list[str] = Field(default_factory=list)
    # Source file and locator of each expected record (keys are expected record IDs).
    expected_locators: dict[str, SourceLocator] = Field(default_factory=dict)
    expected_findings: list[ExpectedFinding] = Field(default_factory=list)
    # For unanswerable or conflicting questions: the analysis must say so, and must
    # not assert any claim (every claim, if any, has status "unresolved").
    expect_unresolved: bool = False
    # Text that must never appear in the output, e.g. the payload of an injected instruction.
    must_not_contain: list[str] = Field(default_factory=list)
    exclusion: Exclusion | None = None
    # How a human checked this answer key, and anything they were unsure about.
    validated_by: str | None = None
    uncertainty_notes: str | None = None

    @model_validator(mode="after")
    def _locators_name_expected_records(self) -> "Question":
        stray = sorted(set(self.expected_locators) - set(self.expected_record_ids))
        if stray:
            raise ValueError(f"expected_locators for records not expected: {', '.join(stray)}")
        return self


class QuestionSet(_Model):
    case_id: str = Field(min_length=1)
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_question_ids(self) -> "QuestionSet":
        seen: set[str] = set()
        for question in self.questions:
            if question.question_id in seen:
                raise ValueError(f"duplicate question_id {question.question_id!r}")
            seen.add(question.question_id)
        return self
