"""Request bodies for the board endpoints (BLA-31).

Responses are the ``InvestigationBoard`` contract JSON itself. Nothing in a request
can set a pinned item's evidence or status: the server copies both from the source
revision, so investigator input only ever reaches the board as notes.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Id = Annotated[str, StringConstraints(min_length=1, max_length=200)]
NoteText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Author = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

MAX_CHANGES = 200


class BoardCreate(BaseModel):
    """Body of ``POST /cases/{case_id}/boards``. IDs, revision and times are server-set."""

    model_config = ConfigDict(extra="forbid")

    title: Title
    active_analysis_id: Id | None = None


class PinRequest(BaseModel):
    """A finding to pin: which revision it is in, what kind it is and its ID there."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["claim", "edge", "node", "event"]
    source_analysis_id: Id
    ref_id: Id


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: NoteText
    # An item on the board after this change (including one pinned in the same request);
    # null or absent for a board-level note.
    target_item_id: Id | None = None
    author: Author = "investigator"


class BoardChange(BaseModel):
    """Body of ``PATCH /cases/{case_id}/boards/{board_id}``. Applied in order: unpin,
    pin, then notes. Every successful change appends one snapshot.

    ``expected_revision``: the revision the client last saw; a 409 if the board has
    moved on since, so two tabs cannot silently drop each other's changes.
    ``active_analysis_id``: send null to clear it; leave it out to keep it.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)] | None = None
    title: Title | None = None
    active_analysis_id: Id | None = None
    pin: Annotated[list[PinRequest], Field(max_length=MAX_CHANGES)] = []
    unpin: Annotated[list[Id], Field(max_length=MAX_CHANGES)] = []
    notes: Annotated[list[NoteCreate], Field(max_length=MAX_CHANGES)] = []
