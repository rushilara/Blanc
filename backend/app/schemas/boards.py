"""Request bodies for the board endpoints (BLA-31).

Responses are the ``InvestigationBoard`` contract JSON itself. Nothing in a request
can set a pinned item's evidence or status: the server copies both from the source
revision. Investigator input reaches the board only as notes (including corrections) and
as each item's ``review_status`` (BLA-48), which are kept apart from the model's output.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.contracts import BoardNoteKind, ReviewStatus

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Id = Annotated[str, StringConstraints(min_length=1, max_length=200)]
# An ID that comes from a stored revision (an analysis ID, or a finding's ID inside one).
# The contract sets no maximum length, so neither does a request that names one: a cap
# here would make long, valid IDs impossible to pin or select.
ContractId = Annotated[str, StringConstraints(min_length=1)]
NoteText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]

MAX_CHANGES = 200

FindingKind = Literal["claim", "edge", "node", "event"]
ALL_KINDS: tuple[FindingKind, ...] = ("claim", "node", "edge", "event")


class BoardCreate(BaseModel):
    """Body of ``POST /cases/{case_id}/boards``. IDs, revision and times are server-set."""

    model_config = ConfigDict(extra="forbid")

    title: Title
    active_analysis_id: ContractId | None = None


class PinRequest(BaseModel):
    """A finding to pin: which revision it is in, what kind it is and its ID there."""

    model_config = ConfigDict(extra="forbid")

    kind: FindingKind
    source_analysis_id: ContractId
    ref_id: ContractId


class AddReconstruction(BaseModel):
    """Pin every finding of one revision at once ("add this reconstruction to board").

    ``kinds`` limits which kinds are added (default: all). Nodes that cite no evidence are
    skipped, since a board item must cite at least one record.
    """

    model_config = ConfigDict(extra="forbid")

    analysis_id: ContractId
    kinds: Annotated[list[FindingKind], Field(min_length=1)] = list(ALL_KINDS)


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: NoteText
    # An item on the board after this change (including one pinned in the same request);
    # null or absent for a board-level note.
    target_item_id: Id | None = None
    # "correction" flags the targeted finding as wrong and says why (BLA-48).
    kind: BoardNoteKind = "note"
    # No author field: the server sets it (see app.boards.NOTE_AUTHOR), so a client cannot
    # label a note as coming from the model.

    @model_validator(mode="after")
    def _correction_needs_target(self) -> "NoteCreate":
        if self.kind == "correction" and self.target_item_id is None:
            raise ValueError("a correction must target a board item (target_item_id)")
        return self


class ReviewChange(BaseModel):
    """Set the investigator's review of one pinned finding (BLA-48)."""

    model_config = ConfigDict(extra="forbid")

    item_id: Id
    review_status: ReviewStatus


class BoardChange(BaseModel):
    """Body of ``PATCH /cases/{case_id}/boards/{board_id}``. Applied in order: unpin,
    add_reconstruction, pin, review, then notes. Every successful change appends one
    snapshot, so each review decision stays in the board's history.

    ``expected_revision``: the revision the client last saw; a 409 if the board has
    moved on since, so two tabs cannot silently drop each other's changes.
    ``active_analysis_id``: send null to clear it; leave it out to keep it.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)] | None = None
    title: Title | None = None
    active_analysis_id: ContractId | None = None
    add_reconstruction: AddReconstruction | None = None
    pin: Annotated[list[PinRequest], Field(max_length=MAX_CHANGES)] = []
    unpin: Annotated[list[Id], Field(max_length=MAX_CHANGES)] = []
    review: Annotated[list[ReviewChange], Field(max_length=MAX_CHANGES)] = []
    notes: Annotated[list[NoteCreate], Field(max_length=MAX_CHANGES)] = []
