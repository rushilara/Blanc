"""Which retrieved images and PDFs the analysis model sees, and their bytes (BLA-71).

Retrieval (BLA-70) puts at most ``RETRIEVAL_MAX_MEDIA`` image/PDF records in the evidence
window, where they also count toward ``RETRIEVAL_MAX_RECORDS``. ``select_media`` then
decides which of them are sent to Gemini, as **inline bytes** in the analysis
``generate_content`` call (``types.Part.from_bytes``; the Files API is not used), and
loads them:

- Records are taken in retrieval rank order. Excluded records are never considered
  (``permitted_records`` has already checked there are none; this module checks again).
- ``media_limit``: the record would exceed ``ANALYSIS_MAX_IMAGES`` / ``ANALYSIS_MAX_PDFS``
  or the total ``ANALYSIS_MAX_MEDIA_BYTES``. Later, smaller files can still fit.
- ``media_too_large``: the single file is over ``ANALYSIS_MAX_MEDIA_FILE_BYTES``. It cannot
  be sent inline whatever the other caps are; it is reported, never uploaded instead.
- ``media``: the stored file is missing from ``CASES_DIR``, has no SHA-256 recorded at
  import, or fails its type / SHA-256 check (``app.media.resolve_case_media``). Altered
  or unverifiable evidence is never sent.

Every retrieved image/PDF that is not sent is returned in ``skipped`` with its reason and
ends up in the revision's ``retrieval_coverage``. Skipped media is removed from the
records handed to the model, so it is not in ``retrieved_record_ids`` and a citation of it
is stripped like any other unretrieved ID.

Bytes are sent **as stored**: EXIF (including GPS) and PDF document info are not stripped.
``app.media_metadata`` reads those fields locally so the prompt can show them as a labelled
text block next to the file.

Defaults (``app.config``) stay well below Gemini's 20 MB inline request limit, which
counts the base64-encoded bytes (~4/3 larger) plus all prompt text.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.config import Settings
from app.contracts import EvidenceRecord
from app.media import MediaIntegrityError, case_media_file, resolve_case_media
from app.media_metadata import extract_file_metadata
from app.retrieval import RetrievalResult

logger = logging.getLogger(__name__)

MediaSkipReason = Literal["media", "media_limit", "media_too_large"]

DEFAULT_MAX_IMAGES = 6
DEFAULT_MAX_PDFS = 2
DEFAULT_MAX_MEDIA_BYTES = 12_000_000
DEFAULT_MAX_MEDIA_FILE_BYTES = 8_000_000


@dataclass(frozen=True)
class AnalysisMediaConfig:
    """Caps on the media sent to the analysis model, and where imported media lives.

    ``cases_dir`` None means the configured ``CASES_DIR``."""

    cases_dir: Path | None = None
    max_images: int = DEFAULT_MAX_IMAGES
    max_pdfs: int = DEFAULT_MAX_PDFS
    max_total_bytes: int = DEFAULT_MAX_MEDIA_BYTES
    max_file_bytes: int = DEFAULT_MAX_MEDIA_FILE_BYTES

    @classmethod
    def from_settings(cls, settings: Settings) -> AnalysisMediaConfig:
        return cls(
            cases_dir=settings.CASES_DIR,
            max_images=settings.ANALYSIS_MAX_IMAGES,
            max_pdfs=settings.ANALYSIS_MAX_PDFS,
            max_total_bytes=settings.ANALYSIS_MAX_MEDIA_BYTES,
            max_file_bytes=settings.ANALYSIS_MAX_MEDIA_FILE_BYTES,
        )

    def resolved_cases_dir(self) -> Path:
        if self.cases_dir is not None:
            return Path(self.cases_dir)
        from app.config import get_settings

        return Path(get_settings().CASES_DIR)


@dataclass(frozen=True)
class MediaPart:
    """One image or PDF to send inline, tagged with its record."""

    record_id: str
    mime_type: str
    data: bytes = field(repr=False)  # never logged or echoed
    sha256: str
    # Locally extracted EXIF / PDF info (untrusted evidence; may be empty).
    file_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MediaSelection:
    """``retrieval`` with unsent media removed, the parts to send, and what was skipped."""

    retrieval: RetrievalResult
    parts: dict[str, MediaPart]
    skipped: dict[str, MediaSkipReason]


def select_media(
    retrieval: RetrievalResult,
    records: Iterable[EvidenceRecord],
    *,
    excluded_record_ids: Iterable[str] = (),
    config: AnalysisMediaConfig | None = None,
) -> MediaSelection:
    """Pick and load the retrieved images/PDFs to send; see the module docstring.

    ``records`` is the case corpus (records of ``retrieval.case_id``); a media record not in
    it is skipped as ``media``. Never raises for a bad file: it is skipped instead.
    """
    cfg = config or AnalysisMediaConfig()
    excluded = {*retrieval.excluded_record_ids, *excluded_record_ids}
    by_id = {r.record_id: r for r in records if r.case_id == retrieval.case_id}
    cases_dir = cfg.resolved_cases_dir()
    caps = {"image": cfg.max_images, "pdf": cfg.max_pdfs}
    counts = {"image": 0, "pdf": 0}
    total = 0
    parts: dict[str, MediaPart] = {}
    skipped: dict[str, MediaSkipReason] = {}
    for retrieved in retrieval.records:
        if retrieved.modality == "text" or retrieved.record_id in excluded:
            continue
        record_id = retrieved.record_id
        record = by_id.get(record_id)
        if record is None or record.content_type is None:
            skipped[record_id] = "media"
            continue
        if counts[retrieved.modality] >= caps[retrieved.modality]:
            skipped[record_id] = "media_limit"
            continue
        path = case_media_file(record, cases_dir, retrieval.case_id)
        if path is None:
            logger.warning("media for %s is not in the case folder; not sent", record_id)
            skipped[record_id] = "media"
            continue
        size = path.stat().st_size
        if size > cfg.max_file_bytes:
            skipped[record_id] = "media_too_large"
            continue
        if total + size > cfg.max_total_bytes:
            skipped[record_id] = "media_limit"
            continue
        try:
            media = resolve_case_media(record, cases_dir, retrieval.case_id)
        except MediaIntegrityError as error:
            logger.warning("media for %s failed its integrity check; not sent: %s", record_id,
                           error)
            media = None
        # Only files whose SHA-256 matches the one recorded at import are sent.
        if media is None or not media.verified or len(media.data) != size:
            skipped[record_id] = "media"
            continue
        counts[retrieved.modality] += 1
        total += size
        parts[record_id] = MediaPart(
            record_id=record_id,
            mime_type=media.media_type,
            data=media.data,
            sha256=media.sha256,
            file_metadata=extract_file_metadata(media.data, media.media_type),
        )
    kept = [
        r
        for r in retrieval.records
        if r.record_id not in excluded and (r.modality == "text" or r.record_id in parts)
    ]
    narrowed = (
        retrieval
        if len(kept) == len(retrieval.records)
        else retrieval.model_copy(update={"records": kept})
    )
    return MediaSelection(retrieval=narrowed, parts=parts, skipped=skipped)


def media_inputs(parts: Mapping[str, MediaPart]) -> dict[str, dict[str, Any]]:
    """``parts`` in the provider-independent shape ``evidence_prompt`` takes."""
    return {
        record_id: {
            "mime_type": part.mime_type,
            "data": part.data,
            "file_metadata": part.file_metadata,
        }
        for record_id, part in parts.items()
    }
