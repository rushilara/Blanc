"""BLA-70: images and PDFs in the evidence window.

Media ranks in the same fused list as text, enters the window as a record with no text
excerpt (the FTS sidecar is never shown as content), is capped by ``max_media``, and is
served to the UI from the case folder. Media the analysis model is not given (over the
vision caps of BLA-71, see tests/test_vision_analysis.py) is listed in the revision's
``retrieval_coverage`` so nothing is dropped silently.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from _paths import CONTRACTS_DIR, load_json
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from test_evidence_media import PDF_BYTES, PNG_BYTES, write_bytes

from app.analysis_llm import FakeAnalysisLLM
from app.config import Settings
from app.contracts import AnalysisRevision, EvidenceRecord, SkippedEvidence
from app.embeddings import FakeEmbedder, MediaInput
from app.evidence import import_file
from app.evidence_store import store_records
from app.evidence_window import coverage_of, left_out
from app.main import create_app
from app.retrieval import RetrievalConfig, retrieve
from app.validation import validate_revision

REVISION_SCHEMA = Draft202012Validator(load_json(CONTRACTS_DIR / "analysis_revision.schema.json"))
TEXT = "Alex emails Jordan about the warehouse key."


class SteeredEmbedder(FakeEmbedder):
    """Questions mentioning ``zebra`` embed exactly like the stored PNG."""

    def embed_query(self, text: str) -> list[float]:
        if "zebra" in text:
            return FakeEmbedder().embed_media([MediaInput(PNG_BYTES, "image/png")])[0]
        return super().embed_query(text)


def make_client(tmp_path: Path, **settings: Any) -> TestClient:
    app = create_app(
        Settings(
            _env_file=None,
            DATABASE_URL=f"sqlite:///{tmp_path / 'media-window.db'}",
            CASES_DIR=tmp_path / "cases",
            **settings,
        )
    )
    return TestClient(app)


def seed(client: TestClient, tmp_path: Path, *, title: str = "Media", pdf: bool = False):
    """A case with one text record and one imported image (and optionally a PDF)."""
    case_id = client.post("/cases", json={"title": title}).json()["case_id"]
    media_dir = tmp_path / "cases" / case_id / "media"
    src = tmp_path / f"src-{case_id}"
    image = import_file(
        write_bytes(src, "zebra.png", PNG_BYTES), case_id, source_root=src, media_dir=media_dir
    )
    records = list(image)
    if pdf:
        records += import_file(
            write_bytes(src, "zebra-report.pdf", PDF_BYTES),
            case_id,
            source_root=src,
            media_dir=media_dir,
        )
    text = EvidenceRecord.model_validate(
        {
            "record_id": "rec-text",
            "case_id": case_id,
            "source_file": "notes.txt",
            "source_locator": "line 1",
            "content": TEXT,
        }
    )
    embedder = SteeredEmbedder()
    client.app.state.embedder = embedder
    with client.app.state.session_factory() as session:
        store_records(session, case_id, [text, *records], embedder, media_dir=media_dir)
        session.commit()
    return case_id, records


def output(cited: str = "rec-text") -> dict[str, Any]:
    return {
        "answer": "Alex mentioned the key.",
        "claims": [
            {"id": "claim-1", "text": "Alex emailed Jordan.", "evidence_ids": [cited],
             "status": "observed"}
        ],
        "graph": {"nodes": [], "edges": []},
        "timeline_events": [],
        "unresolved_questions": [],
    }


# --- Retrieval ------------------------------------------------------------------------


def test_search_returns_media_with_no_excerpt(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        case_id, (image,) = seed(client, tmp_path)
        body = client.post(f"/cases/{case_id}/search", json={"question": "zebra"}).json()
        top = body["records"][0]
        assert top["record_id"] == image.record_id
        assert (top["modality"], top["excerpts"], top["content_type"]) == (
            "image", [], "image/png"
        )
        assert top["media_path"] == image.media_path
        text = next(r for r in body["records"] if r["record_id"] == "rec-text")
        assert text["modality"] == "text" and text["excerpts"][0]["text"] == TEXT


def test_media_beyond_the_cap_is_skipped_not_dropped(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        case_id, records = seed(client, tmp_path, pdf=True)
        with client.app.state.session_factory() as session:
            result = retrieve(
                session, case_id, "zebra", SteeredEmbedder(),
                config=RetrievalConfig(max_media=1),
            )
            none = retrieve(
                session, case_id, "zebra", SteeredEmbedder(),
                config=RetrievalConfig(max_media=0),
            )
    kept = [r for r in result.records if r.modality != "text"]
    capped = [s for s in result.skipped if s.reason == "media_limit"]
    assert len(kept) == 1 and len(capped) == 1
    assert {kept[0].record_id, capped[0].record_id} == {r.record_id for r in records}
    assert capped[0].modality in ("image", "pdf") and capped[0].excerpt_chars is None
    coverage = coverage_of(result, "standard", max_records=16, max_excerpt_chars=32000)
    reasons = {e.record_id: (e.reason, e.modality) for e in coverage.skipped}
    # Without a vision selection (media_skips), the kept one is listed as "media" (not
    # analyzed); the one over RETRIEVAL_MAX_MEDIA keeps its reason, media_limit (BLA-71).
    assert reasons[kept[0].record_id][0] == "media"
    assert reasons[capped[0].record_id] == ("media_limit", capped[0].modality)
    sent = coverage_of(
        result, "standard", max_records=16, max_excerpt_chars=32000, media_skips={}
    )
    assert {e.record_id for e in sent.skipped if e.modality} == {capped[0].record_id}
    assert [r.modality for r in none.records] == ["text"]
    assert {s.record_id for s in none.skipped if s.reason == "media_limit"} == {
        r.record_id for r in records
    }


def test_media_never_takes_a_text_slot_and_never_triggers_the_offer(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        case_id, (image,) = seed(client, tmp_path)
        with client.app.state.session_factory() as session:
            # One text slot, and the image outranks the text: both are kept.
            result = retrieve(
                session, case_id, "zebra key", SteeredEmbedder(),
                config=RetrievalConfig(max_records=1),
            )
    assert [(r.record_id, r.modality) for r in result.records] == [
        (image.record_id, "image"),
        ("rec-text", "text"),
    ]
    assert result.skipped == [] and result.dropped_by_limit == 0
    coverage = coverage_of(result, "standard", max_records=1, max_excerpt_chars=32000)
    entry = next(s for s in coverage.skipped if s.record_id == image.record_id)
    assert (entry.reason, entry.modality) == ("media", "image")
    assert image.record_id not in coverage.left_out_record_ids


def test_media_over_the_cap_does_not_push_text_out_of_the_window(tmp_path: Path) -> None:
    """Review of #78: five images matching the question outrank four text records. With
    four text slots and one media slot, every text record still reaches the model."""
    with make_client(tmp_path) as client:
        case_id = client.post("/cases", json={"title": "Photos"}).json()["case_id"]
        media_dir = tmp_path / "cases" / case_id / "media"
        src = tmp_path / "photos"
        images = [
            rec
            for i in range(5)
            for rec in import_file(
                write_bytes(src, f"zebra_{i}.png", PNG_BYTES),
                case_id,
                source_root=src,
                media_dir=media_dir,
            )
        ]
        texts = [
            EvidenceRecord.model_validate(
                {"record_id": f"rec-text-{i}", "case_id": case_id, "source_file": "notes.txt",
                 "source_locator": f"line {i}", "content": f"Note {i} about the zebra key."}
            )
            for i in range(4)
        ]
        with client.app.state.session_factory() as session:
            store_records(session, case_id, [*images, *texts], SteeredEmbedder(),
                          media_dir=media_dir)
            session.commit()
            result = retrieve(
                session, case_id, "zebra", SteeredEmbedder(),
                config=RetrievalConfig(max_records=4, max_media=1),
            )
    assert result.skipped and result.skipped[0].modality == "image"  # media outranks text
    kept = [r for r in result.records if r.modality == "text"]
    assert sorted(r.record_id for r in kept) == [t.record_id for t in texts]
    assert sum(r.modality == "image" for r in result.records) == 1
    assert [s.reason for s in result.skipped] == ["media_limit"] * 4
    assert result.dropped_by_limit == 0
    coverage = coverage_of(result, "standard", max_records=4, max_excerpt_chars=32000)
    # Over RETRIEVAL_MAX_MEDIA: media_limit; the kept one, with no vision selection: media.
    assert sorted(s.reason for s in coverage.skipped) == ["media"] + ["media_limit"] * 4


def test_media_first_does_not_block_truncating_the_first_text_excerpt(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        case_id, (image,) = seed(client, tmp_path)
        with client.app.state.session_factory() as session:
            result = retrieve(
                session, case_id, "zebra", SteeredEmbedder(),  # image first, text by vector
                config=RetrievalConfig(max_excerpt_chars=5),
            )
    assert result.records[0].record_id == image.record_id
    text = next(r for r in result.records if r.modality == "text")
    assert text.excerpts[0].text == TEXT[:5] and text.excerpts[0].truncated


# --- Analysis: media the model is not shown is listed and cannot be cited ---------------
# Vision analysis (BLA-71) sends retrieved media inline; tests/test_vision_analysis.py
# covers that. With the image cap at 0 nothing is sent, which is the BLA-70 behaviour.


def test_analysis_never_gives_unsent_media_to_the_model_and_lists_it(tmp_path: Path) -> None:
    with make_client(tmp_path, ANALYSIS_MAX_IMAGES=0) as client:
        case_id, (image,) = seed(client, tmp_path)
        fake = FakeAnalysisLLM(responses=[output()])
        client.app.state.analysis_llm = fake
        response = client.post(f"/cases/{case_id}/analyses", json={"question": "zebra key"})
        assert response.status_code == 201, response.text
        body = response.json()
    assert not list(REVISION_SCHEMA.iter_errors(body))
    prompt = json.loads(fake.calls[-1][1]["content"])
    assert [r["record_id"] for r in prompt["source_records"]] == ["rec-text"]
    assert image.record_id not in json.dumps(fake.calls[-1])
    assert body["retrieved_record_ids"] == ["rec-text"]
    media = [s for s in body["retrieval_coverage"]["skipped"] if "modality" in s]
    assert media == [
        {"record_id": image.record_id, "rank": 1, "reason": "media_limit", "vector_rank": 1,
         "keyword_rank": 1, "modality": "image"}
    ]
    assert body["retrieval_coverage"]["offer_expanded_window"] is False


def test_the_model_cannot_cite_media_it_was_not_shown(tmp_path: Path) -> None:
    with make_client(tmp_path, ANALYSIS_MAX_IMAGES=0) as client:
        case_id, (image,) = seed(client, tmp_path)
        fake = FakeAnalysisLLM(responses=[output(image.record_id), output(image.record_id)])
        client.app.state.analysis_llm = fake
        body = client.post(f"/cases/{case_id}/analyses", json={"question": "zebra key"}).json()
    assert body["needs_review"] is True  # the citation was stripped, not accepted
    assert all(image.record_id not in c["evidence_ids"] for c in body["claims"])


# --- Serving media to the UI ------------------------------------------------------------


def test_media_endpoint_serves_the_imported_file_with_its_hash(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        case_id, (image, pdf) = seed(client, tmp_path, pdf=True)
        record = client.get(f"/cases/{case_id}/records/{image.record_id}").json()
        assert record["excerpt"] is None
        assert (record["record"]["modality"], record["record"]["content"]) == ("image", "")
        got = client.get(f"/cases/{case_id}/records/{image.record_id}/media")
        assert got.status_code == 200 and got.content == PNG_BYTES
        assert got.headers["content-type"] == "image/png"
        assert got.headers["x-hash-verified"] == "true"
        assert got.headers["content-security-policy"] == "default-src 'none'; sandbox"
        doc = client.get(f"/cases/{case_id}/records/{pdf.record_id}/media")
        assert doc.status_code == 200 and doc.content == PDF_BYTES
        assert doc.headers["content-type"] == "application/pdf"
        assert doc.headers["content-disposition"] == f'attachment; filename="{pdf.record_id}.pdf"'
        assert "content-disposition" not in got.headers

        # Altered on disk after import: refused, never served.
        (tmp_path / "cases" / case_id / image.media_path).write_bytes(PNG_BYTES + b"x")
        assert client.get(f"/cases/{case_id}/records/{image.record_id}/media").status_code == 409


def test_media_is_case_scoped(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        case_a, (image,) = seed(client, tmp_path, title="A")
        case_b, _ = seed(client, tmp_path, title="B")
        assert client.get(f"/cases/{case_b}/records/{image.record_id}/media").status_code in (
            200,
            404,
        )
        # Same record ID in both cases (stable IDs hash the case): never the other's file.
        assert image.record_id not in {
            r["record_id"] for r in client.get(f"/cases/{case_b}/records").json()["items"]
        }
        assert client.get(f"/cases/{case_b}/records/{image.record_id}/media").status_code == 404
        assert client.get(f"/cases/{case_a}/records/{image.record_id}/media").status_code == 200


# --- Validation and contract ------------------------------------------------------------


def _revision_with(skipped: list[dict[str, Any]], tmp_path: Path):
    with make_client(tmp_path) as client:
        case_id, (image,) = seed(client, tmp_path)
        client.app.state.analysis_llm = FakeAnalysisLLM(responses=[output()])
        body = client.post(f"/cases/{case_id}/analyses", json={"question": "zebra key"}).json()
        with client.app.state.session_factory() as session:
            from app.analyses import _records_for

            revision = AnalysisRevision.model_validate(body)
            records = _records_for(session, revision)
    data = copy.deepcopy(body)
    data["retrieval_coverage"]["skipped"] = [
        {**s, "record_id": s["record_id"].replace("IMAGE", image.record_id)} for s in skipped
    ]
    return records, AnalysisRevision.model_validate(data)


@pytest.mark.parametrize(
    ("skip", "message"),
    [
        ({"record_id": "IMAGE", "rank": 1, "reason": "media"}, "as media but gives no modality"),
        ({"record_id": "IMAGE", "rank": 1, "reason": "budget", "modality": "image"},
         "uses no character budget"),
    ],
)
def test_validation_rejects_inconsistent_media_skips(tmp_path, skip, message) -> None:
    records, revision = _revision_with([skip], tmp_path)
    errors = validate_revision(records, revision)
    assert any(message in e for e in errors), errors


def test_media_skips_never_count_as_left_out() -> None:
    skipped = [
        SkippedEvidence(record_id="img", rank=2, reason="record_limit", vector_rank=1,
                        modality="image"),
        SkippedEvidence(record_id="txt", rank=3, reason="record_limit", vector_rank=1),
    ]
    assert left_out(skipped, 1) == ["txt"]
    schema = load_json(CONTRACTS_DIR / "analysis_revision.schema.json")
    item = Draft202012Validator(
        schema["properties"]["retrieval_coverage"]["properties"]["skipped"]["items"]
    )
    assert item.is_valid({"record_id": "a", "rank": 1, "reason": "media", "modality": "pdf"})
    assert not item.is_valid({"record_id": "a", "rank": 1, "reason": "media"})
    assert not item.is_valid(
        {"record_id": "a", "rank": 1, "reason": "budget", "modality": "image"}
    )
