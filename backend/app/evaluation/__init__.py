"""Evaluation: run a human-verified question set against pipeline output (BLA-45)."""

from app.evaluation.questions import Question, QuestionSet
from app.evaluation.runner import CheckResult, FileResults, evaluate, evaluate_question

__all__ = [
    "CheckResult",
    "FileResults",
    "Question",
    "QuestionSet",
    "evaluate",
    "evaluate_question",
]
