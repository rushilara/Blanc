"""Provider-independent prompt assembly for BLA-16; no model calls or storage."""

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "evidence_analysis.md").read_text(
    encoding="utf-8"
)


def build_evidence_messages(
    *,
    case_id: str,
    question: str,
    records: Iterable[Mapping[str, Any]],
    excluded_record_ids: Iterable[str] = (),
    prior_analysis: str | None = None,
) -> list[dict[str, str]]:
    """Keep trusted instructions separate from JSON-encoded source data.

    Call after retrieval. Exclusion is repeated here as defense in depth; it
    does not replace filtering all chunks before retrieval/ranking. The caller
    must use server-loaded records, never records supplied by the end user.
    """
    if not case_id.strip() or not question.strip():
        raise ValueError("case_id and question must be nonempty")
    excluded = set(excluded_record_ids)
    allowed = []
    seen = set()
    for record in records:
        if record.get("case_id") != case_id:
            raise ValueError("Source record belongs to a different case")
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError("Source record needs a nonempty record_id")
        if record_id in excluded:
            continue
        if record_id in seen:
            raise ValueError("Duplicate record_id; resolve chunks before prompting")
        for field in ("source_file", "source_locator", "content"):
            if not isinstance(record.get(field), str):
                raise ValueError(f"Source record needs a string {field}")
        seen.add(record_id)
        allowed.append(dict(record))
    payload = {
        "case_id": case_id,
        "investigator_question": question,
        "source_records": allowed,
    }
    if prior_analysis is not None:
        payload["context"] = {
            "label": "prior analysis — not evidence",
            "text": prior_analysis,
        }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
