"""Response models for the source record endpoints (BLA-59).

Records are serialized with ``EvidenceRecord.to_contract_json`` (via a wrap serializer, so
the OpenAPI schema still references ``EvidenceRecord``). Plain ``model_dump`` would write
omitted fields (``timestamp_kind``, ``metadata``) as null, which
``contracts/evidence_record.schema.json`` rejects.
"""

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

from app.contracts import EvidenceRecord


class ExcerptRead(BaseModel):
    """``text == record.content[char_start:char_end]`` (Python str / code point indices).
    ``chunk_id`` is set when the excerpt was requested by chunk, else null."""

    text: str
    char_start: int
    char_end: int
    chunk_id: str | None


class RecordRead(BaseModel):
    """``record`` is exactly the EvidenceRecord contract JSON; ``excerpt`` is null unless
    ``chunk_id`` or ``char_start``/``char_end`` was requested."""

    record: EvidenceRecord
    excerpt: ExcerptRead | None

    @model_serializer(mode="wrap")
    def _contract_record(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        data["record"] = self.record.to_contract_json()
        return data


class RecordPage(BaseModel):
    """One page of a case's records; ``total`` is the case's record count."""

    items: list[EvidenceRecord]
    total: int
    limit: int
    offset: int

    @model_serializer(mode="wrap")
    def _contract_items(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        data["items"] = [r.to_contract_json() for r in self.items]
        return data


class FileContextRead(BaseModel):
    """The cited record among the other records of its ``source_file`` in this case.

    ``items[index - start]`` is the cited record. ``ordered_by`` says how the file was
    ordered: ``time`` (every record has a time), ``locator`` (none has; the position the
    source locator states), or ``time_then_locator`` (timed records first)."""

    source_file: str
    total: int
    index: int
    start: int
    ordered_by: str
    items: list[EvidenceRecord]

    @model_serializer(mode="wrap")
    def _contract_items(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        data["items"] = [r.to_contract_json() for r in self.items]
        return data
