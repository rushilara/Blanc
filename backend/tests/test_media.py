"""GET /cases/{case_id}/records/{record_id}/media: a record's original image, local only."""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.contracts import EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.main import create_app
from app.media import media_source_paths, normalize_source_path
from app.models import Case

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
HISTORY = "Users/Test/AppData/Local/Google/Chrome/User Data/Default/History"


def download(case_id: str, record_id: str, target: str) -> EvidenceRecord:
    content = json.dumps({"target_path": target, "url_chain": [{"url": "https://example.test/x"}]})
    return EvidenceRecord.model_validate(
        {
            "record_id": record_id,
            "case_id": case_id,
            "source_file": HISTORY,
            "source_locator": f"SQLite downloads.id={record_id}",
            "content": content,
        }
    )


def write(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An evidence folder laid out like data/narcos: extracted files plus a manifest."""
    root = tmp_path / "evidence"
    write(root, "artifacts/inode-1.PNG", PNG)
    write(root, "Users/Test/Pictures/photo.jpg", JPEG)  # at its original path, no manifest
    write(root, "artifacts/inode-2.png", b"not really a png")
    write(root, "artifacts/inode-3.png", PNG)
    entries = [
        {
            "source_path": "Users/Test/Documents/flights.PNG",
            "extracted_file": "artifacts/inode-1.PNG",
            "sha256": hashlib.sha256(PNG).hexdigest(),
        },
        {
            "source_path": "Users/Test/Documents/fake.png",
            "extracted_file": "artifacts/inode-2.png",
            "sha256": None,
        },
        {
            "source_path": "Users/Test/Documents/swapped.png",
            "extracted_file": "artifacts/inode-3.png",
            "sha256": "0" * 64,
        },
        {
            "source_path": "Users/Test/Documents/escape.png",
            "extracted_file": "../outside.png",
            "sha256": None,
        },
    ]
    (root / "artifact_manifest.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries), "utf-8"
    )
    write(tmp_path, "outside.png", PNG)
    (root / "Users/Test/Pictures/link.png").symlink_to(tmp_path / "outside.png")
    return root


@pytest.fixture
def client_and_cases(tmp_path: Path, root: Path) -> Iterator[tuple[TestClient, str, str]]:
    settings = Settings(
        _env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'db.sqlite'}", EVIDENCE_ROOT=root
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            a, b = Case(title="A"), Case(title="B")
            session.add_all([a, b])
            session.flush()
            targets = {
                "flights": r"C:\Users\Test\Documents\flights.PNG",
                "photo": r"C:\Users\Test\Pictures\photo.jpg",
                "missing": r"C:\Users\Test\Pictures\gone.jpg",
                "installer": r"C:\Users\Test\Downloads\setup.exe",
                "fake": r"C:\Users\Test\Documents\fake.png",
                "swapped": r"C:\Users\Test\Documents\swapped.png",
                "escape": r"C:\Users\Test\Documents\escape.png",
                "symlink": r"C:\Users\Test\Pictures\link.png",
                "traversal": r"C:\Users\..\..\outside.png",
            }
            store_records(
                session,
                a.case_id,
                [download(a.case_id, k, v) for k, v in targets.items()],
                FakeEmbedder(),
            )
            session.commit()
            yield client, a.case_id, b.case_id


def media(client: TestClient, case_id: str, record_id: str):
    return client.get(f"/cases/{case_id}/records/{record_id}/media")


def test_serves_a_manifest_image_with_a_verified_hash(client_and_cases) -> None:
    client, a, _ = client_and_cases
    response = media(client, a, "flights")
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-hash-verified"] == "true"
    assert response.headers["x-source-path"] == "Users/Test/Documents/flights.PNG"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in response.headers["cache-control"]


def test_serves_a_file_at_its_original_path_without_a_manifest_entry(client_and_cases) -> None:
    client, a, _ = client_and_cases
    response = media(client, a, "photo")
    assert (response.status_code, response.content) == (200, JPEG)
    assert response.headers["x-hash-verified"] == "false"


@pytest.mark.parametrize("record_id", ["missing", "installer", "escape", "symlink", "traversal"])
def test_unavailable_non_image_and_out_of_root_files_are_not_served(
    client_and_cases, record_id: str
) -> None:
    client, a, _ = client_and_cases
    assert media(client, a, record_id).status_code == 404


@pytest.mark.parametrize("record_id", ["fake", "swapped"])
def test_a_wrong_type_or_hash_is_refused(client_and_cases, record_id: str) -> None:
    client, a, _ = client_and_cases
    response = media(client, a, record_id)
    assert response.status_code == 409


def test_media_is_case_scoped(client_and_cases) -> None:
    client, _, b = client_and_cases
    assert media(client, b, "flights").status_code == 404
    assert client.get("/cases/case_missing/records/flights/media").status_code == 404


def test_without_an_evidence_root_nothing_is_served(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'db.sqlite'}"))
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            case = Case(title="A")
            session.add(case)
            session.flush()
            store_records(
                session, case.case_id, [download(case.case_id, "x", r"C:\a.png")], FakeEmbedder()
            )
            session.commit()
            case_id = case.case_id
        response = media(client, case_id, "x")
        assert response.status_code == 404
        assert "EVIDENCE_ROOT" in response.json()["detail"]


def test_path_normalisation_refuses_unsafe_paths() -> None:
    assert normalize_source_path(r"C:\Users\A\x.png") == "Users/A/x.png"
    assert normalize_source_path("/Users/A/x.png") == "Users/A/x.png"
    assert normalize_source_path(r"C:\Users\..\x.png") is None
    assert normalize_source_path("") is None


def test_only_image_paths_are_candidates() -> None:
    record = download("c", "r", r"C:\Users\A\setup.exe")
    assert media_source_paths(record) == []
    image_record = EvidenceRecord.model_validate(
        {
            "record_id": "r",
            "case_id": "c",
            "source_file": "Users/A/scan.JPG",
            "source_locator": "file",
            "content": "OCR text",
        }
    )
    assert media_source_paths(image_record) == ["Users/A/scan.JPG"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_evidence_root_means_no_images(blank: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    monkeypatch.setenv("EVIDENCE_ROOT", blank)
    assert Settings(_env_file=None).EVIDENCE_ROOT is None
