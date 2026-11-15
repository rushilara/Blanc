"""Record identifiers for imported evidence.

The record model itself is the shared contract model, `app.contracts.EvidenceRecord`
(BLA-5), so imported records can go straight to `app.evidence_store.store_records`.
It is re-exported here for the importer.
"""

import hashlib

from app.contracts import EvidenceRecord
from app.contracts import RecordTimestampKind as TimestampKind

__all__ = ["EvidenceRecord", "TimestampKind", "stable_record_id"]


def stable_record_id(case_id: str, source_file: str, source_locator: str) -> str:
    """Deterministic ID from where the record came from, not from when it was imported.

    Importing the same file into the same case again yields the same IDs, so a
    re-import can be deduplicated instead of creating a second copy. That is why
    this is a hash and not a random `app.ids.new_id("rec")`; it keeps the same
    `rec_` prefix so record IDs look alike across the codebase.
    """
    key = "\x1f".join((case_id, source_file, source_locator))
    return "rec_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
