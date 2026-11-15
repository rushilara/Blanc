"""BLA-49: structured case report export (Markdown + JSON) for boards and revisions."""

import copy
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from _paths import RAW
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analyses import save_revision
from app.contracts import AnalysisRevision, EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.models import Case
from app.report_markdown import code, escape_inline, escape_paragraphs, fenced, render_markdown
from app.reports import MAX_EXCERPT_CHARS, analysis_report, board_report
from app.routers.reports import get_report_clock

BASE_ID = "analysis-synthetic-1"
CHILD_ID = "analysis-synthetic-2"
EVIL_ID = "analysis-evil"
GRANDCHILD_ID = "analysis-synthetic-3"
FIXED = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

EVIL_TEXT = (
    "<script>alert('x')</script>\n"
    "# Injected heading\n"
    "[click me](http://evil.example/steal) ![img](http://evil.example/i.png)\n"
    "| col | col |\n|---|---|\n"
    "Run `rm -rf` then ``` and ```` to break fences\n"
    "<img src=x onerror=alert(1)> www.evil.example https://evil.example\n"
    "1. not a list\n- not a bullet\n> not a quote"
)


@dataclass
class Seeded:
    case_id: str
    other_case_id: str
    board_id: str
    other_board_id: str
    note_ids: list[str]


def _revision(case_id: str, key: str, **changes: object) -> AnalysisRevision:
    return AnalysisRevision.model_validate(
        dict(copy.deepcopy(RAW[key]), case_id=case_id, **changes)
    )


def _evil_revision(case_id: str) -> AnalysisRevision:
    return AnalysisRevision.model_validate(
        {
            "schema_version": "1.0",
            "analysis_id": EVIL_ID,
            "parent_analysis_id": None,
            "case_id": case_id,
            "question": "# What about <b>this</b>? [x](http://evil.example)",
            "excluded_record_ids": [],
            "retrieved_record_ids": ["rec-evil", "rec-long"],
            "answer": EVIL_TEXT + "\n\nSecond paragraph | with pipe.",
            "claims": [
                {
                    "id": "claim-`x`",
                    "text": EVIL_TEXT,
                    "evidence_ids": ["rec-evil"],
                    "status": "inferred",
                },
                {
                    "id": "claim-long",
                    "text": "Long one.",
                    "evidence_ids": ["rec-long"],
                    "status": "unresolved",
                },
            ],
            "graph": {
                "nodes": [
                    {
                        "id": "a",
                        "label": "<b>A</b>",
                        "type": "person",
                        "evidence_ids": ["rec-evil"],
                    },
                    {"id": "b", "label": "**B**", "type": "other"},
                ],
                "edges": [
                    {
                        "id": "edge-x",
                        "source": "a",
                        "target": "b",
                        "type": "linked|to",
                        "evidence_ids": ["rec-evil"],
                        "status": "inferred",
                        "description": "[desc](javascript:alert(1))",
                    }
                ],
            },
            "timeline_events": [
                {
                    "id": "ev-approx",
                    "description": "Approximate <i>event</i>",
                    "timestamp": "2026",
                    "timestamp_precision": "approximate",
                    "evidence_ids": ["rec-evil"],
                    "status": "inferred",
                },
                {
                    "id": "ev-nozone",
                    "description": "No zone event",
                    "timestamp": "2026-04-10T09:15:00",
                    "evidence_ids": ["rec-evil"],
                    "status": "reported",
                },
                {
                    "id": "ev-undated",
                    "description": "Undated event",
                    "timestamp": "last Friday",
                    "evidence_ids": ["rec-evil"],
                    "status": "reported",
                },
                {
                    "id": "ev-day",
                    "description": "Day event",
                    "timestamp": "2026-04-09",
                    "timestamp_precision": "day",
                    "evidence_ids": ["rec-evil"],
                    "status": "observed",
                },
            ],
            "unresolved_questions": ["<script>q</script>"],
            "created_at": "2026-11-14T22:00:00Z",
        }
    )


OTHER_CASE_MARKER = " OTHER-CASE-CONTENT-MUST-NOT-LEAK"


def _records(case_id: str, *, other_case: bool = False) -> list[EvidenceRecord]:
    records = [EvidenceRecord.model_validate(dict(r, case_id=case_id)) for r in RAW["records"]]
    if other_case:
        # Same record IDs as the primary case, different content — catches missing case_id
        # filters that would otherwise still pass by coincidence.
        records = [r.model_copy(update={"content": r.content + OTHER_CASE_MARKER}) for r in records]
    records.append(
        EvidenceRecord(
            record_id="rec-evil",
            case_id=case_id,
            source_file="evil|<b>file</b>.txt",
            source_locator="line `1`",
            content=EVIL_TEXT + (OTHER_CASE_MARKER if other_case else ""),
            timestamp="2026-04-09",
            timestamp_kind="stated",
            metadata={"attachments": [{"locator": "part 2", "filename": "photo.jpg"}]},
        )
    )
    records.append(
        EvidenceRecord(
            record_id="rec-long",
            case_id=case_id,
            source_file="long.txt",
            source_locator="entire file",
            content=("abcdefghij" * 300) + (OTHER_CASE_MARKER if other_case else ""),
        )
    )
    return records


@pytest.fixture
def seeded(app: FastAPI, client: TestClient) -> Seeded:
    with app.state.session_factory() as session:
        case, other = Case(title="Report <case>"), Case(title="Other case")
        session.add_all([case, other])
        session.commit()
        store_records(session, case.case_id, _records(case.case_id), FakeEmbedder())
        store_records(
            session, other.case_id, _records(other.case_id, other_case=True), FakeEmbedder()
        )
        session.commit()
        case_id, other_id = case.case_id, other.case_id
        save_revision(session, _revision(case_id, "revision"))
        save_revision(session, _revision(case_id, "child"))
        # Restore then re-exclude: first continuous exclusion streak starts at REEXCLUDE_ID.
        save_revision(
            session,
            _revision(
                case_id,
                "revision",
                analysis_id="analysis-synthetic-restore",
                parent_analysis_id=CHILD_ID,
                excluded_record_ids=[],
                created_at="2026-11-14T20:30:00Z",
            ),
        )
        save_revision(
            session,
            _revision(
                case_id,
                "child",
                analysis_id="analysis-synthetic-reexclude",
                parent_analysis_id="analysis-synthetic-restore",
                created_at="2026-11-14T20:45:00Z",
            ),
        )
        # A follow-up child of the first exclusion: rec-02 stays excluded (inherited).
        save_revision(
            session,
            _revision(
                case_id,
                "child",
                analysis_id=GRANDCHILD_ID,
                parent_analysis_id=CHILD_ID,
                created_at="2026-11-14T21:00:00Z",
            ),
        )
        save_revision(session, _evil_revision(case_id))
        save_revision(session, _revision(other_id, "revision", analysis_id="analysis-other"))

    app.dependency_overrides[get_report_clock] = lambda: lambda: FIXED

    board = client.post(
        f"/cases/{case_id}/boards", json={"title": "Alex | Casey", "active_analysis_id": CHILD_ID}
    ).json()
    pins = [
        {"kind": "claim", "source_analysis_id": BASE_ID, "ref_id": "claim-1"},
        {"kind": "claim", "source_analysis_id": BASE_ID, "ref_id": "claim-2"},
        {"kind": "edge", "source_analysis_id": BASE_ID, "ref_id": "edge-1"},
        {"kind": "node", "source_analysis_id": BASE_ID, "ref_id": "alex"},
        {"kind": "event", "source_analysis_id": BASE_ID, "ref_id": "event-2"},
        {"kind": "event", "source_analysis_id": BASE_ID, "ref_id": "event-1"},
    ]
    response = client.patch(f"/cases/{case_id}/boards/{board['board_id']}", json={"pin": pins})
    assert response.status_code == 200, response.text
    items = {(i["kind"], i["ref_id"]): i["item_id"] for i in response.json()["items"]}
    response = client.patch(
        f"/cases/{case_id}/boards/{board['board_id']}",
        json={
            "notes": [
                {
                    "text": "Check the delivery.\n# Not a heading",
                    "target_item_id": items[("claim", "claim-1")],
                },
                {"text": "Board-level <script>note</script>"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    note_ids = [n["note_id"] for n in response.json()["notes"]]
    other_board = client.post(f"/cases/{other_id}/boards", json={"title": "Other"}).json()
    return Seeded(case_id, other_id, board["board_id"], other_board["board_id"], note_ids)


def export(client: TestClient, case_id: str, what: str, ident: str, fmt: str, expect: int = 200):
    response = client.get(f"/cases/{case_id}/{what}/{ident}/export", params={"format": fmt})
    assert response.status_code == expect, response.text
    return response


SECTIONS = [
    "## Report details",
    "## Questions and answers",
    "## Claims",
    "## Entities",
    "## Relationships",
    "## Timeline",
    "### Dated events (normalized order)",
    "### Uncertain times (order not established)",
    "### Undated events",
    "## Sources",
    "## Excluded evidence",
    "## Investigator notes",
    "## Limitations",
]


def _outside_fences(markdown: str) -> list[str]:
    """Lines not inside a fenced code block (fences opened by this renderer)."""
    lines, fence = [], None
    for line in markdown.split("\n"):
        if fence is None:
            match = re.fullmatch(r"(`{3,})text", line)
            if match:
                fence = match.group(1)
                continue
            lines.append(line)
        elif line == fence:
            fence = None
    assert fence is None, "unclosed fence"
    return lines


def _fenced_blocks(markdown: str) -> list[str]:
    blocks, fence, body = [], None, []
    for line in markdown.split("\n"):
        if fence is None:
            match = re.fullmatch(r"(`{3,})text", line)
            if match:
                fence, body = match.group(1), []
        elif line == fence:
            blocks.append("\n".join(body))
            fence = None
        else:
            body.append(line)
    return blocks


def _strip_code_spans(line: str) -> str:
    return re.sub(r"(`+)(?!`).*?(?<!`)\1(?!`)", "", line)


# --- Board export -------------------------------------------------------------------------


def test_board_json_has_every_section_and_mirrors_the_board(client, seeded) -> None:
    response = export(client, seeded.case_id, "boards", seeded.board_id, "json")
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    report = response.json()
    assert report["report_version"] == "1.0" and report["report_kind"] == "board"
    assert report["generated_at"] == "2026-11-15T12:00:00Z"
    assert report["case"] == {"case_id": seeded.case_id, "title": "Report <case>"}
    assert report["board"]["board_id"] == seeded.board_id
    assert report["board"]["active_analysis_id"] == CHILD_ID

    analyses = {a["analysis_id"]: a for a in report["analyses"]}
    assert [a["analysis_id"] for a in report["analyses"]] == [CHILD_ID, BASE_ID]
    assert analyses[CHILD_ID]["role"] == "board_active"
    assert analyses[BASE_ID]["role"] == "board_source"
    assert analyses[CHILD_ID]["lineage"] == [BASE_ID, CHILD_ID]
    assert analyses[BASE_ID]["answer"] == RAW["revision"]["answer"]

    assert [c["claim_id"] for c in report["claims"]] == ["claim-1", "claim-2"]
    assert all(c["status"] == "reported" for c in report["claims"])
    claim = report["claims"][0]
    assert claim["text"] == RAW["revision"]["claims"][0]["text"]
    assert claim["evidence_ids"] == ["rec-01"] and claim["board_item_id"].startswith("item_")
    assert claim["note_ids"] == [seeded.note_ids[0]]

    (edge,) = report["relationships"]
    assert edge["source"] == {"node_id": "alex", "label": "Alex", "type": "person"}
    assert edge["target"]["label"] == "Jordan"
    assert (edge["type"], edge["status"], edge["description"]) == (
        "communicated_with",
        "reported",
        "Email communication",
    )
    assert [e["node_id"] for e in report["entities"]] == ["alex"]
    # Normalized order, not pin order.
    assert [e["event_id"] for e in report["timeline"]["dated"]] == ["event-1", "event-2"]
    assert report["timeline"]["dated"][0]["display"] == "2026-04-10T09:15:00Z"
    assert report["timeline"]["uncertain"] == report["timeline"]["undated"] == []
    assert report["limitations"]


def test_board_sources_list_each_cited_record_once_with_exact_excerpt(client, seeded) -> None:
    report = export(client, seeded.case_id, "boards", seeded.board_id, "json").json()
    sources = {s["record_id"]: s for s in report["sources"]}
    assert [s["record_id"] for s in report["sources"]] == ["rec-01", "rec-02"]
    for raw in RAW["records"]:
        source = sources[raw["record_id"]]
        assert (source["source_file"], source["source_locator"]) == (
            raw["source_file"],
            raw["source_locator"],
        )
        assert (source["timestamp"], source["timestamp_kind"]) == (
            raw["timestamp"],
            raw["timestamp_kind"],
        )
        if raw["record_id"] == "rec-02":
            # Excluded in the board's active revision: locator stays, excerpt does not.
            assert source["excerpt"] is None
            assert source["excluded_in"] == [CHILD_ID]
            continue
        excerpt = source["excerpt"]
        assert excerpt["text"] == raw["content"][excerpt["char_start"] : excerpt["char_end"]]
        assert excerpt["truncated"] is False
    cited = {(r["kind"], r["ref_id"]) for r in sources["rec-01"]["cited_by"]}
    assert cited == {
        ("claim", "claim-1"),
        ("node", "alex"),
        ("edge", "edge-1"),
        ("event", "event-1"),
    }


def test_board_excluded_evidence_is_listed_and_never_support(client, seeded) -> None:
    report = export(client, seeded.case_id, "boards", seeded.board_id, "json").json()
    (exclusion,) = report["excluded_evidence"]
    assert exclusion == {
        "analysis_id": CHILD_ID,
        "record_id": "rec-02",
        "inherited": False,
        "first_excluded_in": CHILD_ID,
        "source_file": "chat.json",
        "source_locator": "messages[7]",
        "timestamp": "2026-04-10T10:30:00Z",
    }
    # rec-02 is cited by claim-2 pinned from the base revision, which still included it;
    # the source entry says it is excluded in the active revision, and the excerpt is omitted.
    rec02 = next(s for s in report["sources"] if s["record_id"] == "rec-02")
    assert rec02["excluded_in"] == [CHILD_ID]
    assert rec02["excerpt"] is None
    assert all(ref["analysis_id"] == BASE_ID for ref in rec02["cited_by"])
    assert RAW["records"][1]["content"] not in str(report["sources"])
    assert any("excluded in another revision" in text for text in report["limitations"])

    markdown = export(client, seeded.case_id, "boards", seeded.board_id, "md").text
    assert RAW["records"][1]["content"] not in markdown
    assert "Excerpt: omitted" in markdown


def test_first_excluded_in_restarts_after_restore(client, seeded) -> None:
    report = export(
        client, seeded.case_id, "analyses", "analysis-synthetic-reexclude", "json"
    ).json()
    (exclusion,) = report["excluded_evidence"]
    assert exclusion["inherited"] is False
    assert exclusion["first_excluded_in"] == "analysis-synthetic-reexclude"
    assert exclusion["analysis_id"] == "analysis-synthetic-reexclude"


def test_stale_exclusion_pin_does_not_hide_restored_excerpt(client, seeded) -> None:
    # Active revision permits rec-02; a pin from the earlier exclusion still flags it.
    board = client.post(
        f"/cases/{seeded.case_id}/boards",
        json={
            "title": "Restored active",
            "active_analysis_id": "analysis-synthetic-restore",
        },
    ).json()
    response = client.patch(
        f"/cases/{seeded.case_id}/boards/{board['board_id']}",
        json={
            "pin": [
                {
                    "kind": "claim",
                    "source_analysis_id": "analysis-synthetic-restore",
                    "ref_id": "claim-2",
                },
                {"kind": "claim", "source_analysis_id": CHILD_ID, "ref_id": "claim-1"},
            ]
        },
    )
    assert response.status_code == 200, response.text

    report = export(client, seeded.case_id, "boards", board["board_id"], "json").json()
    rec02 = next(s for s in report["sources"] if s["record_id"] == "rec-02")
    assert rec02["excluded_in"] == [CHILD_ID]
    assert rec02["excerpt"] is not None
    assert rec02["excerpt"]["text"] == RAW["records"][1]["content"]
    assert all(ref["analysis_id"] == "analysis-synthetic-restore" for ref in rec02["cited_by"])

    markdown = export(client, seeded.case_id, "boards", board["board_id"], "md").text
    assert RAW["records"][1]["content"] in markdown
    assert "Excerpt: omitted" not in markdown
    assert "the excerpt is shown" in markdown


@pytest.mark.parametrize(
    "pins",
    [
        # Permitting source first, then excluding source — pin order must not matter.
        [
            {"kind": "claim", "source_analysis_id": BASE_ID, "ref_id": "claim-2"},
            {"kind": "claim", "source_analysis_id": CHILD_ID, "ref_id": "claim-1"},
        ],
        [
            {"kind": "claim", "source_analysis_id": CHILD_ID, "ref_id": "claim-1"},
            {"kind": "claim", "source_analysis_id": BASE_ID, "ref_id": "claim-2"},
        ],
    ],
    ids=["permit_then_exclude", "exclude_then_permit"],
)
def test_board_without_active_withholds_when_any_revision_excludes(client, seeded, pins) -> None:
    # No active revision → no current evidence state; withhold if any reported revision
    # excludes the record, regardless of pin order.
    board = client.post(f"/cases/{seeded.case_id}/boards", json={"title": "No active"}).json()
    assert board["active_analysis_id"] is None
    response = client.patch(
        f"/cases/{seeded.case_id}/boards/{board['board_id']}", json={"pin": pins}
    )
    assert response.status_code == 200, response.text

    report = export(client, seeded.case_id, "boards", board["board_id"], "json").json()
    assert report["board"]["active_analysis_id"] is None
    rec02 = next(s for s in report["sources"] if s["record_id"] == "rec-02")
    assert rec02["excluded_in"] == [CHILD_ID]
    assert rec02["excerpt"] is None
    assert RAW["records"][1]["content"] not in str(report["sources"])

    markdown = export(client, seeded.case_id, "boards", board["board_id"], "md").text
    assert RAW["records"][1]["content"] not in markdown
    assert "Excerpt: omitted" in markdown


def test_board_notes_are_separate_and_attributed(client, seeded) -> None:
    report = export(client, seeded.case_id, "boards", seeded.board_id, "json").json()
    notes = report["investigator_notes"]
    assert [n["note_id"] for n in notes] == seeded.note_ids
    assert all(n["author"] == "investigator" for n in notes)
    assert notes[0]["target"] == {"kind": "claim", "analysis_id": BASE_ID, "ref_id": "claim-1"}
    assert notes[1]["target"] is None and notes[1]["target_item_id"] is None
    # Note text appears nowhere in the AI-generated parts.
    ai = [report["claims"], report["relationships"], report["analyses"], report["timeline"]]
    assert "Check the delivery" not in str(ai)

    markdown = export(client, seeded.case_id, "boards", seeded.board_id, "md").text
    notes_at = markdown.index("## Investigator notes")
    limits_at = markdown.index("## Limitations")
    assert markdown.index("Check the delivery") > notes_at
    assert markdown.index("Check the delivery") < limits_at
    assert markdown.count("Check the delivery") == 1
    assert "Written by the investigator, not by the model" in markdown


def test_board_markdown_has_all_sections_in_order(client, seeded) -> None:
    response = export(client, seeded.case_id, "boards", seeded.board_id, "md")
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    disposition = response.headers["content-disposition"]
    assert disposition == (
        f'attachment; filename="blanc-report-{seeded.case_id}-{seeded.board_id}.md"'
    )
    markdown = response.text
    positions = [markdown.index(f"\n{heading}\n") for heading in SECTIONS]
    assert positions == sorted(positions)
    assert markdown.startswith("# Case report: Report \\<case\\>\n")
    assert "Generated at (UTC): 2026-11-15T12:00:00Z" in markdown
    assert f"Parent chain (oldest first): `{BASE_ID}` -> `{CHILD_ID}`" in markdown
    assert "Nothing in this report has been verified" in markdown
    assert "- EXCLUDED `rec-02` from analysis" in markdown
    assert markdown.count("### Source `rec-01`") == 1
    assert "- **reported** Alex -> Jordan: communicated\\_with" in markdown
    excluded_section = markdown[markdown.index("## Excluded evidence") :]
    excluded_section = excluded_section[: excluded_section.index("## Investigator notes")]
    assert RAW["records"][1]["content"] not in excluded_section


def test_board_limitations_cover_the_required_points(client, seeded) -> None:
    report = export(client, seeded.case_id, "boards", seeded.board_id, "json").json()
    text = " ".join(report["limitations"])
    for phrase in [
        "AI-generated",
        "require human review",
        "does not verify",
        "changes what the analysis can conclude, not what happened",
        "Retrieval may have missed relevant records",
        "stored locally",
        "Gemini",
        "were not ingested",
    ]:
        assert phrase in text, phrase


def test_board_export_of_an_earlier_snapshot(client, seeded) -> None:
    response = client.get(
        f"/cases/{seeded.case_id}/boards/{seeded.board_id}/export",
        params={"format": "json", "revision": 1},
    )
    assert response.status_code == 200
    report = response.json()
    assert report["board"]["revision"] == 1
    assert report["claims"] == [] and report["investigator_notes"] == []
    assert report["sources"] == []
    markdown = client.get(
        f"/cases/{seeded.case_id}/boards/{seeded.board_id}/export", params={"revision": 1}
    ).text
    sources = markdown[markdown.index("## Sources") : markdown.index("## Excluded evidence")]
    assert sources.rstrip().endswith("shown literally.\n\nNone.")
    assert "## Investigator notes" in markdown


# --- Revision export ----------------------------------------------------------------------


def test_analysis_export_json(client, seeded) -> None:
    report = export(client, seeded.case_id, "analyses", BASE_ID, "json").json()
    assert report["report_kind"] == "analysis" and report["board"] is None
    (analysis,) = report["analyses"]
    assert analysis["role"] == "exported" and analysis["lineage"] == [BASE_ID]
    assert analysis["question"] == RAW["revision"]["question"]
    assert analysis["unresolved_questions"] == RAW["revision"]["unresolved_questions"]
    assert [c["claim_id"] for c in report["claims"]] == ["claim-1", "claim-2"]
    assert [e["node_id"] for e in report["entities"]] == ["alex", "jordan", "casey"]
    assert [e["edge_id"] for e in report["relationships"]] == ["edge-1", "edge-2"]
    assert [s["record_id"] for s in report["sources"]] == ["rec-01", "rec-02"]
    assert report["excluded_evidence"] == []
    assert report["investigator_notes"] == []
    assert all(s["excluded_in"] == [] for s in report["sources"])


def test_analysis_export_markdown(client, seeded) -> None:
    response = export(client, seeded.case_id, "analyses", CHILD_ID, "md")
    markdown = response.text
    for heading in SECTIONS:
        assert f"\n{heading}\n" in markdown
    assert "None. Notes live on boards; export a board to include them." in markdown
    assert "### Source `rec-02`" not in markdown  # excluded, so not cited
    assert "- EXCLUDED `rec-02`" in markdown
    assert "(excluded in this revision)" in markdown


def test_inherited_exclusions_are_marked(client, seeded) -> None:
    report = export(client, seeded.case_id, "analyses", GRANDCHILD_ID, "json").json()
    (exclusion,) = report["excluded_evidence"]
    assert exclusion["inherited"] is True and exclusion["first_excluded_in"] == CHILD_ID
    assert report["analyses"][0]["lineage"] == [BASE_ID, CHILD_ID, GRANDCHILD_ID]
    assert "rec-02" not in {s["record_id"] for s in report["sources"]}
    markdown = export(client, seeded.case_id, "analyses", GRANDCHILD_ID, "md").text
    assert f"(inherited from `{CHILD_ID}`)" in markdown


def test_analysis_timeline_groups_and_other_details(client, seeded) -> None:
    report = export(client, seeded.case_id, "analyses", EVIL_ID, "json").json()
    timeline = report["timeline"]
    assert [e["event_id"] for e in timeline["dated"]] == ["ev-day"]
    assert timeline["dated"][0]["precision"] == "day"
    assert [e["event_id"] for e in timeline["uncertain"]] == ["ev-approx", "ev-nozone"]
    assert timeline["uncertain"][1]["timezone_known"] is False
    assert [e["event_id"] for e in timeline["undated"]] == ["ev-undated"]
    assert timeline["undated"][0]["display"] is None
    long = next(s for s in report["sources"] if s["record_id"] == "rec-long")
    assert long["excerpt"]["truncated"] is True
    assert long["excerpt"]["char_end"] == MAX_EXCERPT_CHARS
    assert long["excerpt"]["content_chars"] == 3000
    text = " ".join(report["limitations"])
    assert (
        "photo.jpg" in text and "cut to their first" in text and "order is not established" in text
    )
    entity_b = next(e for e in report["entities"] if e["node_id"] == "b")
    assert entity_b["evidence_ids"] == []


# --- Case isolation -------------------------------------------------------------------------


def test_cross_case_and_unknown_ids_are_404(client, seeded) -> None:
    for fmt in ("md", "json"):
        export(client, seeded.other_case_id, "boards", seeded.board_id, fmt, 404)
        export(client, seeded.case_id, "boards", seeded.other_board_id, fmt, 404)
        export(client, seeded.other_case_id, "analyses", BASE_ID, fmt, 404)
        export(client, seeded.case_id, "analyses", "analysis-other", fmt, 404)
        export(client, seeded.case_id, "analyses", "nope", fmt, 404)
        export(client, "case_nope", "boards", seeded.board_id, fmt, 404)
    response = client.get(
        f"/cases/{seeded.case_id}/boards/{seeded.board_id}/export", params={"revision": 99}
    )
    assert response.status_code == 404
    # The other case's own export works and sees only its own data.
    report = export(client, seeded.other_case_id, "analyses", "analysis-other", "json").json()
    assert report["case"]["case_id"] == seeded.other_case_id
    assert {s["record_id"] for s in report["sources"]} == {"rec-01", "rec-02"}
    # Same IDs as the primary case, but only the other-case content.
    for source in report["sources"]:
        assert source["excerpt"] is not None
        assert OTHER_CASE_MARKER in source["excerpt"]["text"]
    primary = export(client, seeded.case_id, "analyses", BASE_ID, "json").json()
    assert OTHER_CASE_MARKER not in str(primary)
    for source in primary["sources"]:
        assert source["excerpt"] is not None
        assert OTHER_CASE_MARKER not in source["excerpt"]["text"]


def test_unknown_format_is_422(client, seeded) -> None:
    export(client, seeded.case_id, "analyses", BASE_ID, "pdf", 422)


# --- Untrusted content ---------------------------------------------------------------------


def test_untrusted_content_cannot_inject_markdown_or_html(client, seeded) -> None:
    markdown = export(client, seeded.case_id, "analyses", EVIL_ID, "md").text
    outside = _outside_fences(markdown)
    our_headings = set(SECTIONS) | {"# Case report: Report \\<case\\>"}
    for line in outside:
        prose = _strip_code_spans(line)
        if line.startswith("#"):
            assert line in our_headings or re.fullmatch(r"### (Analysis|Source|Note) `.+`", line), (
                line
            )
        assert not re.search(r"(?<!\\)[<\[\]|]", prose), line  # no raw HTML, links or pipes
        assert not re.search(r"(?<!\\)!(?=\\?\[)", prose), line
        assert "://" not in prose and "www." not in prose.lower(), line
        assert not line.startswith(("1.", "> not", "- not", "|")), line

    # The evidence excerpt survives verbatim inside a fence it cannot close.
    assert EVIL_TEXT in _fenced_blocks(markdown)
    assert "\n`````text\n" in markdown  # longest backtick run in EVIL_TEXT is 4

    # Escaped AI text still reads as the same words.
    assert "\\<script\\>alert('x')\\</script\\>" in markdown
    assert "\\[click me\\](http:\\/\\/evil.example/steal) \\!\\[img\\]" in markdown
    assert "\\[desc\\](javascript:alert(1))" in markdown
    # An ID with backticks stays one code span.
    assert "``claim-`x` ``" not in markdown and "`` claim-`x` ``" in markdown


def test_notes_with_markup_are_fenced(client, seeded) -> None:
    markdown = export(client, seeded.case_id, "boards", seeded.board_id, "md").text
    blocks = _fenced_blocks(markdown)
    assert "Check the delivery.\n# Not a heading" in blocks
    assert "Board-level <script>note</script>" in blocks
    assert "\n# Not a heading" not in "\n".join(_outside_fences(markdown))


def test_escape_helpers() -> None:
    assert escape_inline("a\nb\t c") == "a b c"
    assert escape_inline("# h") == "\\# h"
    assert escape_inline("- item") == "\\- item"
    assert escape_inline("12. x") == "12\\. x"
    assert escape_inline("> q") == "\\> q"
    assert escape_inline("a\x00b\u2028c") == "abc"
    assert escape_inline("see WWW.x.com") == "see WWW\\.x.com"
    assert escape_paragraphs("a\n- b\n\n\n# c\r\n===\n") == ["a\\\n\\- b", "\\# c\\\n\\==="]
    assert escape_paragraphs("  \n") == []
    assert code("") == "` `"
    assert code("a``b") == "```a``b```"
    assert code("`edge`") == "`` `edge` ``"
    assert fenced("x\r\ny") == ["```text", "x", "y", "```"]
    assert fenced("````") == ["`````text", "````", "`````"]


# --- Determinism --------------------------------------------------------------------------


def test_output_is_deterministic(app, client, seeded) -> None:
    for what, ident in (("boards", seeded.board_id), ("analyses", EVIL_ID)):
        first = export(client, seeded.case_id, what, ident, "md").content
        second = export(client, seeded.case_id, what, ident, "md").content
        assert first == second
        assert (
            export(client, seeded.case_id, what, ident, "json").content
            == export(client, seeded.case_id, what, ident, "json").content
        )

    # A different generated-at changes only that line.
    with app.state.session_factory() as session:
        case = session.get(Case, seeded.case_id)
        a = render_markdown(board_report(session, case, seeded.board_id, now=FIXED))
        later = datetime(2027, 1, 1, tzinfo=UTC)
        b = render_markdown(board_report(session, case, seeded.board_id, now=later))
        diff = [(x, y) for x, y in zip(a.split("\n"), b.split("\n"), strict=True) if x != y]
        assert diff == [
            (
                "- Generated at (UTC): 2026-11-15T12:00:00Z",
                "- Generated at (UTC): 2027-01-01T00:00:00Z",
            )
        ]
        assert analysis_report(session, case, BASE_ID, now=FIXED) == analysis_report(
            session, case, BASE_ID, now=FIXED
        )


def test_default_clock_and_format(client, seeded, app) -> None:
    app.dependency_overrides.pop(get_report_clock)
    response = client.get(f"/cases/{seeded.case_id}/analyses/{BASE_ID}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert "- Generated at (UTC): 20" in response.text


def test_export_routes_are_in_openapi(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/cases/{case_id}/boards/{board_id}/export" in paths
    assert "/cases/{case_id}/analyses/{analysis_id}/export" in paths


def test_needs_review_revision_is_flagged(app, client, seeded) -> None:
    with app.state.session_factory() as session:
        save_revision(
            session,
            _revision(
                seeded.case_id,
                "revision",
                analysis_id="analysis-partial",
                answer="",
                needs_review=True,
                validation_errors=["claim-9 cited a record that was not retrieved"],
            ),
        )
    report = export(client, seeded.case_id, "analyses", "analysis-partial", "json").json()
    assert report["analyses"][0]["needs_review"] is True
    assert any("claim-9 cited a record" in text for text in report["limitations"])
    markdown = export(client, seeded.case_id, "analyses", "analysis-partial", "md").text
    assert "> **Needs review.**" in markdown
    assert "No answer was saved." in markdown


def test_board_review_status_and_corrections_are_exported_apart(client, seeded) -> None:
    """BLA-48: review status sits next to each pinned finding; corrections are notes."""
    board = client.get(f"/cases/{seeded.case_id}/boards/{seeded.board_id}").json()
    claim_item = next(i for i in board["items"] if i["kind"] == "claim")
    correction = "Investigator correction: wrong person."
    response = client.patch(
        f"/cases/{seeded.case_id}/boards/{seeded.board_id}",
        json={
            "review": [{"item_id": claim_item["item_id"], "review_status": "disputed"}],
            "notes": [
                {"text": correction, "kind": "correction", "target_item_id": claim_item["item_id"]}
            ],
        },
    )
    assert response.status_code == 200, response.text

    report = export(client, seeded.case_id, "boards", seeded.board_id, "json").json()
    claims = {c["board_item_id"]: c for c in report["claims"]}
    assert claims[claim_item["item_id"]]["review_status"] == "disputed"
    assert claims[claim_item["item_id"]]["status"] == claim_item["status"]  # model label kept
    others = [c for c in report["claims"] if c["board_item_id"] != claim_item["item_id"]]
    assert others and all(c["review_status"] == "unreviewed" for c in others)
    flagged = [n for n in report["investigator_notes"] if n["kind"] == "correction"]
    assert [n["text"] for n in flagged] == [correction]
    assert correction not in json.dumps([report["claims"], report["analyses"]])

    md = export(client, seeded.case_id, "boards", seeded.board_id, "md").text
    assert "Investigator review: disputed (the investigator disputes this finding)" in md
    assert "### Correction" in md
    assert "Flags the attached finding as wrong" in md
    # A single-analysis export has no board, so no review status.
    single = export(client, seeded.case_id, "analyses", claim_item["source_analysis_id"], "json")
    assert all(c["review_status"] is None for c in single.json()["claims"])
