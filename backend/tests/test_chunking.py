import random

import pytest

from app.chunking import approx_tokens, chunk_spans


def check_spans(content: str, spans: list[tuple[int, int]], window: int) -> None:
    assert spans[0][0] == 0
    assert spans[-1][1] == len(content)
    for start, end in spans:
        assert 0 <= start < end <= len(content)
        assert end - start <= window
    for (s1, e1), (s2, _) in zip(spans, spans[1:], strict=False):
        assert s1 < s2 <= e1  # forward progress, no gaps


def test_empty_and_whitespace_have_no_chunks() -> None:
    assert chunk_spans("") == []
    assert chunk_spans(" \n\t ") == []


def test_short_content_is_one_chunk() -> None:
    assert chunk_spans("hello world") == [(0, 11)]
    assert chunk_spans("x" * 1600) == [(0, 1600)]


def test_words_are_not_split() -> None:
    content = " ".join(["word"] * 1000)
    spans = chunk_spans(content, max_tokens=40, overlap_tokens=5)
    check_spans(content, spans, 160)
    for start, end in spans:
        piece = content[start:end]
        assert piece.strip() == piece
        assert set(piece.split(" ")) == {"word"}


def test_unbroken_text_still_splits() -> None:
    content = "a" * 5000
    spans = chunk_spans(content, max_tokens=100, overlap_tokens=10)
    check_spans(content, spans, 400)
    assert spans[1][0] == 400 - 40  # exact overlap when there is no whitespace


@pytest.mark.parametrize("max_tokens,overlap", [(10, 5), (10, -1), (0, 0)])
def test_invalid_parameters(max_tokens: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_spans("text", max_tokens=max_tokens, overlap_tokens=overlap)


@pytest.mark.parametrize("seed", range(20))
def test_random_text_covers_everything_with_overlap(seed: int) -> None:
    rng = random.Random(seed)
    words = ["".join(rng.choices("abcxyz", k=rng.randint(1, 30))) for _ in range(800)]
    content = "".join(w + rng.choice([" ", "  ", "\n", "\n\n", "\t"]) for w in words)
    spans = chunk_spans(content, max_tokens=60, overlap_tokens=10)
    check_spans(content, spans, 240)
    assert len(spans) > 5


def test_approx_tokens() -> None:
    assert approx_tokens("") == 0
    assert approx_tokens("abcd") == 1
    assert approx_tokens("abcde") == 2
