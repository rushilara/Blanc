"""`python -m app.evidence --store`: import straight into the local case database."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import init_db, make_engine, make_session_factory
from app.embeddings import EmbeddingError, FakeEmbedder
from app.evidence.__main__ import main as cli_main
from app.models import Case, EvidenceRecordRow, ImportStatus


class FailingEmbedder(FakeEmbedder):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("quota exceeded")


def export(tmp_path: Path) -> Path:
    source = tmp_path / "exports"
    (source / "chats").mkdir(parents=True)
    messages = [{"text": "Meet at the dock at nine."}, {"text": "Bring the second phone."}]
    (source / "chats" / "chat.json").write_text(json.dumps(messages), encoding="utf-8")
    (source / "notes.txt").write_text("Seen near the warehouse.\n\nLeft by car.", "utf-8")
    return source


def db_state(url: str) -> tuple[list[Case], int]:
    engine = make_engine(url)
    init_db(engine)
    try:
        with make_session_factory(engine)() as session:
            cases = list(session.scalars(select(Case)))
            records = session.scalar(select(func.count()).select_from(EvidenceRecordRow))
            return cases, records or 0
    finally:
        engine.dispose()


@pytest.fixture
def url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'data' / 'blanc.db'}"


def test_store_with_title_creates_a_ready_case_and_prints_its_id(
    tmp_path: Path, url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    embedder = FakeEmbedder()
    args = [str(export(tmp_path)), "--store", "--title", "Dock case", "--database-url", url]

    assert cli_main(args, embedder=embedder) == 0

    case_id = capsys.readouterr().out.strip()
    cases, records = db_state(url)
    assert [(c.case_id, c.title, c.import_status) for c in cases] == [
        (case_id, "Dock case", ImportStatus.READY)
    ]
    assert records == 4
    assert embedder.embedded_texts


def test_rerunning_into_the_same_case_stores_and_embeds_nothing_new(
    tmp_path: Path, url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    source = export(tmp_path)
    assert cli_main([str(source), "--store", "--title", "Dock", "--database-url", url],
                    embedder=FakeEmbedder()) == 0
    case_id = capsys.readouterr().out.strip()

    again = FakeEmbedder()
    args = [str(source), "--store", "--case-id", case_id, "--database-url", url]
    assert cli_main(args, embedder=again) == 0

    output = capsys.readouterr()
    assert output.out.strip() == case_id
    assert "0 new, 4 already stored" in output.err
    assert again.calls == []
    assert db_state(url)[1] == 4


def test_store_into_an_unknown_case_writes_nothing(
    tmp_path: Path, url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [str(export(tmp_path)), "--store", "--case-id", "case_missing", "--database-url", url]

    assert cli_main(args, embedder=FakeEmbedder()) == 1

    assert "does not exist" in capsys.readouterr().err
    assert db_state(url) == ([], 0)


def test_an_embedding_failure_leaves_no_case_and_no_records(
    tmp_path: Path, url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [str(export(tmp_path)), "--store", "--title", "Dock", "--database-url", url]

    assert cli_main(args, embedder=FailingEmbedder()) == 1

    assert "nothing was written" in capsys.readouterr().err
    assert db_state(url) == ([], 0)


def test_a_folder_with_nothing_importable_creates_no_case(tmp_path: Path, url: str) -> None:
    source = tmp_path / "exports"
    source.mkdir()
    (source / "scan.pdf").write_bytes(b"%PDF-1.7")

    args = [str(source), "--store", "--title", "Dock", "--database-url", url]
    assert cli_main(args, embedder=FakeEmbedder()) == 1
    assert db_state(url) == ([], 0)


def test_store_also_writes_out_with_the_real_case_id(
    tmp_path: Path, url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "records.json"
    args = [str(export(tmp_path)), "--store", "--title", "Dock", "--database-url", url,
            "--out", str(out)]

    assert cli_main(args, embedder=FakeEmbedder()) == 0

    case_id = capsys.readouterr().out.strip()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert {item["case_id"] for item in written} == {case_id}


@pytest.mark.parametrize(
    "extra",
    [
        ["--title", "Dock"],  # --title needs --store
        ["--store"],  # neither --case-id nor --title
        ["--store", "--case-id", "case_x", "--title", "Dock"],  # both
        ["--store", "--title", "   "],  # blank title
        ["--case-id", "case_x", "--database-url", "sqlite://"],  # --database-url needs --store
    ],
)
def test_invalid_flag_combinations_are_usage_errors(tmp_path: Path, extra: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli_main([str(export(tmp_path)), *extra], embedder=FakeEmbedder())
    assert exit_info.value.code == 2
