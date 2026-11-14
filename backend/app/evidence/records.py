"""`EvidenceRecord` model, mirroring `contracts/evidence_record.schema.json`.

`tests/test_evidence_import.py` checks that the fields and the `timestamp_kind`
values stay in sync with the schema, so a contract change fails loudly here.
"""

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TimestampKind = Literal["sent", "received", "created", "modified", "observed", "stated", "unknown"]


class EvidenceRecord(BaseModel):
    """One immutable, traceable source record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)
    content: str
    timestamp: str | None = None
    timestamp_kind: TimestampKind = "unknown"
    original_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def stable_record_id(case_id: str, source_file: str, source_locator: str) -> str:
    """Deterministic ID from where the record came from, not from when it was imported.

    Importing the same file into the same case again yields the same IDs, so a
    re-import can be deduplicated instead of creating a second copy. That is why
    this is a hash and not a random `app.ids.new_id("rec")`; it keeps the same
    `rec_` prefix so record IDs look alike across the codebase.
    """
    key = "\x1f".join((case_id, source_file, source_locator))
    return "rec_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
