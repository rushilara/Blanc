"""GET /cases/{case_id}/records/{record_id}/media: a record's original image, local only."""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, unquote

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.contracts import EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.main import create_app
from app.media import (
    MAX_MEDIA_BYTES,
    MediaIntegrityError,
    MediaNotFoundError,
    media_source_paths,
    normalize_source_path,
    resolve_media,
)
from app.models import Case

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + (40).to_bytes(4, "little") + b"WEBP" + b"\x00" * 28
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


def case_evidence(root: Path, case_id: str) -> Path:
    """``EVIDENCE_ROOT/{case_id}/`` — files and manifest are scoped per case."""
    folder = root / case_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Evidence root; per-case folders are filled once case ids are known."""
    return tmp_path / "evidence"


def populate_case_folder(case_dir: Path, tmp_path: Path) -> None:
    """Lay out extracted files and a manifest under one case folder."""
    write(case_dir, "artifacts/inode-1.PNG", PNG)
    write(case_dir, "Users/Test/Pictures/photo.jpg", JPEG)  # at its original path, no manifest
    write(case_dir, "artifacts/inode-2.png", b"not really a png")
    write(case_dir, "artifacts/inode-3.png", PNG)
    write(case_dir, "Users/Test/Documents/café.png", PNG)
    write(case_dir, "artifacts/clip.gif", GIF)
    write(case_dir, "artifacts/shot.webp", WEBP)
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
        {
            "source_path": "Users/Test/Documents/café.png",
            "extracted_file": "Users/Test/Documents/café.png",
            "sha256": hashlib.sha256(PNG).hexdigest(),
        },
        {
            "source_path": "Users/Test/Documents/clip.gif",
            "extracted_file": "artifacts/clip.gif",
            "sha256": None,
        },
        {
            "source_path": "Users/Test/Documents/shot.webp",
            "extracted_file": "artifacts/shot.webp",
            "sha256": None,
        },
        {
            "source_path": "Users/Test/Documents/huge.png",
            "extracted_file": "artifacts/inode-1.PNG",
            "sha256": None,
        },
    ]
    (case_dir / "artifact_manifest.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries), "utf-8"
    )
    write(tmp_path, "outside.png", PNG)
    (case_dir / "Users/Test/Pictures/link.png").symlink_to(tmp_path / "outside.png")


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
            populate_case_folder(case_evidence(root, a.case_id), tmp_path)
            # Case B has the same download rows but no files under its case folder.
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
                "cafe": r"C:\Users\Test\Documents\café.png",
                "gif": r"C:\Users\Test\Documents\clip.gif",
                "webp": r"C:\Users\Test\Documents\shot.webp",
                "huge": r"C:\Users\Test\Documents\huge.png",
            }
            for case in (a, b):
                store_records(
                    session,
                    case.case_id,
                    [download(case.case_id, k, v) for k, v in targets.items()],
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


def test_non_ascii_source_path_is_percent_encoded(client_and_cases) -> None:
    client, a, _ = client_and_cases
    response = media(client, a, "cafe")
    assert response.status_code == 200
    encoded = response.headers["x-source-path"]
    assert encoded == quote("Users/Test/Documents/café.png", safe="/")
    assert unquote(encoded) == "Users/Test/Documents/café.png"
    # latin-1-safe: Starlette must be able to encode the header without raising.
    assert encoded.encode("latin-1")


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


def test_oversized_file_is_refused(client_and_cases, monkeypatch: pytest.MonkeyPatch) -> None:
    client, a, _ = client_and_cases
    monkeypatch.setattr("app.media.MAX_MEDIA_BYTES", 8)
    response = media(client, a, "huge")
    assert response.status_code == 409
    assert "larger" in response.json()["detail"]


@pytest.mark.parametrize(
    ("record_id", "media_type", "body"),
    [("gif", "image/gif", GIF), ("webp", "image/webp", WEBP)],
)
def test_gif_and_webp_are_sniffed_and_served(
    client_and_cases, record_id: str, media_type: str, body: bytes
) -> None:
    client, a, _ = client_and_cases
    response = media(client, a, record_id)
    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == media_type


@pytest.mark.parametrize(
    ("target", "source_path", "extracted", "bad_bytes"),
    [
        (
            r"C:\Users\Test\Documents\lie.gif",
            "Users/Test/Documents/lie.gif",
            "artifacts/lie.gif",
            PNG,
        ),
        (
            r"C:\Users\Test\Documents\lie.webp",
            "Users/Test/Documents/lie.webp",
            "artifacts/lie.webp",
            JPEG,
        ),
    ],
)
def test_gif_and_webp_with_wrong_bytes_are_refused(
    tmp_path: Path, target: str, source_path: str, extracted: str, bad_bytes: bytes
) -> None:
    """A sniff that always returns True would wrongly serve these as 200."""
    root = tmp_path / "evidence"
    case_id = "case_wrongsniff"
    case_dir = root / case_id
    write(case_dir, extracted, bad_bytes)
    (case_dir / "artifact_manifest.jsonl").write_text(
        json.dumps({"source_path": source_path, "extracted_file": extracted, "sha256": None}),
        encoding="utf-8",
    )
    with pytest.raises(MediaIntegrityError):
        resolve_media(download(case_id, "bad", target), root, case_id)


def test_empty_case_id_is_refused(tmp_path: Path, root: Path) -> None:
    with pytest.raises(MediaNotFoundError, match="case_id"):
        resolve_media(download("c", "flights", r"C:\Users\Test\Documents\flights.PNG"), root, "")


def test_media_cors_exposes_hash_headers(client_and_cases) -> None:
    """Cross-origin frontend must read X-Hash-Verified (CORS expose_headers)."""
    client, a, _ = client_and_cases
    response = client.get(
        f"/cases/{a}/records/flights/media",
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    exposed = response.headers.get("access-control-expose-headers", "")
    exposed_l = exposed.lower()
    assert "x-hash-verified" in exposed_l
    assert "x-content-sha256" in exposed_l
    assert "x-source-path" in exposed_l
    assert response.headers["x-hash-verified"] == "true"


def test_media_is_case_scoped(client_and_cases) -> None:
    """Same target_path in two cases: only the case with files under its folder gets 200."""
    client, a, b = client_and_cases
    assert media(client, a, "flights").status_code == 200
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


def linked_root(tmp_path: Path, entries: list[dict[str, object]], files: dict[str, bytes]) -> Path:
    """Evidence root with one case folder ``c`` holding the manifest and files."""
    root = tmp_path / "linked"
    case_dir = case_evidence(root, "c")
    for relative, data in files.items():
        write(case_dir, relative, data)
    (case_dir / "artifact_manifest.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries), "utf-8"
    )
    return root


def test_record_ids_link_a_moved_file_to_its_download(tmp_path: Path) -> None:
    root = linked_root(
        tmp_path,
        [
            {
                "source_path": "Users/Test/Downloads/Misc/BNE.png",
                "extracted_file": "artifacts/a.png",
                "sha256": hashlib.sha256(PNG).hexdigest(),
                "record_ids": ["dl-6"],
            }
        ],
        {"artifacts/a.png": PNG},
    )
    # The download row saved Downloads\BNE.png; the file was later moved to Downloads\Misc.
    found = resolve_media(download("c", "dl-6", r"C:\Users\Test\Downloads\BNE.png"), root, "c")
    assert (found.data, found.source_path, found.verified) == (
        PNG,
        "Users/Test/Downloads/Misc/BNE.png",
        True,
    )
    with pytest.raises(MediaNotFoundError):
        resolve_media(download("c", "dl-7", r"C:\Users\Test\Downloads\BNE.png"), root, "c")
    # Another case id must not see this case's folder.
    with pytest.raises(MediaNotFoundError):
        resolve_media(download("other", "dl-6", r"C:\Users\Test\Downloads\BNE.png"), root, "other")


def test_a_records_own_path_wins_over_a_record_ids_link(tmp_path: Path) -> None:
    root = linked_root(
        tmp_path,
        [
            {
                "source_path": "Users/Test/own.jpg",
                "extracted_file": "artifacts/own.jpg",
                "sha256": None,
            },
            {
                "source_path": "Users/Test/other.png",
                "extracted_file": "artifacts/other.png",
                "sha256": None,
                "record_ids": ["dl-1"],
            },
        ],
        {"artifacts/own.jpg": JPEG, "artifacts/other.png": PNG},
    )
    assert resolve_media(download("c", "dl-1", r"C:\Users\Test\own.jpg"), root, "c").data == JPEG


def test_record_ids_links_keep_every_check(tmp_path: Path) -> None:
    write(tmp_path, "outside.png", PNG)
    root = linked_root(
        tmp_path,
        [
            {
                "source_path": "Users/Test/swapped.png",
                "extracted_file": "artifacts/s.png",
                "sha256": "0" * 64,
                "record_ids": ["swapped"],
            },
            {
                "source_path": "Users/Test/escape.png",
                "extracted_file": "../outside.png",
                "sha256": None,
                "record_ids": ["escape"],
            },
            {
                "source_path": "Users/Test/notes.txt",
                "extracted_file": "artifacts/n.txt",
                "sha256": None,
                "record_ids": ["text"],
            },
            {
                "source_path": "Users/Test/fake.png",
                "extracted_file": "artifacts/f.png",
                "sha256": None,
                "record_ids": ["fake"],
            },
        ],
        {"artifacts/s.png": PNG, "artifacts/n.txt": b"hello", "artifacts/f.png": b"nope"},
    )
    missing = r"C:\Users\Test\gone.png"
    with pytest.raises(MediaIntegrityError):
        resolve_media(download("c", "swapped", missing), root, "c")
    with pytest.raises(MediaIntegrityError):
        resolve_media(download("c", "fake", missing), root, "c")
    for record_id in ("escape", "text"):
        with pytest.raises(MediaNotFoundError):
            resolve_media(download("c", record_id, missing), root, "c")


def test_the_first_entry_naming_a_record_wins_and_bad_lists_are_ignored(tmp_path: Path) -> None:
    root = linked_root(
        tmp_path,
        [
            {
                "source_path": "Users/Test/first.png",
                "extracted_file": "artifacts/1.png",
                "sha256": None,
                "record_ids": ["dl-1"],
            },
            {
                "source_path": "Users/Test/second.jpg",
                "extracted_file": "artifacts/2.jpg",
                "sha256": None,
                "record_ids": ["dl-1"],
            },
            {
                "source_path": "Users/Test/third.jpg",
                "extracted_file": "artifacts/2.jpg",
                "sha256": None,
                "record_ids": "dl-2",
            },
            ["not", "an", "object"],
        ],
        {"artifacts/1.png": PNG, "artifacts/2.jpg": JPEG},
    )
    missing = r"C:\Users\Test\gone.png"
    assert resolve_media(download("c", "dl-1", missing), root, "c").data == PNG
    with pytest.raises(MediaNotFoundError):
        resolve_media(download("c", "dl-2", missing), root, "c")


def test_oversized_via_resolve_media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = linked_root(
        tmp_path,
        [
            {
                "source_path": "Users/Test/big.png",
                "extracted_file": "artifacts/big.png",
                "sha256": None,
            }
        ],
        {"artifacts/big.png": PNG},
    )
    monkeypatch.setattr("app.media.MAX_MEDIA_BYTES", 8)
    with pytest.raises(MediaIntegrityError, match="larger"):
        resolve_media(download("c", "big", r"C:\Users\Test\big.png"), root, "c")
    assert MAX_MEDIA_BYTES == 25 * 1024 * 1024  # import stayed at the real default
