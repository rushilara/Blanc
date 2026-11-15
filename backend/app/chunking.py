"""Split record content into overlapping chunks for embedding and keyword search.

Token counts are approximated as ``len(text) / 4`` characters per token (a common rule of
thumb for English with Gemini/GPT-style tokenizers), so no tokenizer dependency is needed.
Defaults: windows of ~400 tokens (1600 chars) overlapping by ~50 tokens (200 chars).

Spans are half-open ``(start, end)`` character offsets into the original content, so
``content[start:end]`` is always the exact chunk text. Boundaries are moved to whitespace
where possible so words are not cut in half: a window end backs off to the last whitespace
in its second half, and the next window start moves forward to the next word start.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 4
DEFAULT_MAX_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 50


def approx_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)  # ceil


def chunk_spans(
    content: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[tuple[int, int]]:
    """Character spans of the chunks of ``content``.

    Empty or whitespace-only content has no chunks. Content up to ``max_tokens`` is one
    chunk covering all of it. Longer content is split into windows of at most
    ``max_tokens`` that overlap by about ``overlap_tokens``; together they cover every
    character.
    """
    if overlap_tokens < 0 or max_tokens <= 2 * overlap_tokens:
        raise ValueError("need max_tokens > 2 * overlap_tokens >= 0")
    if not content.strip():
        return []
    window = max_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN
    n = len(content)
    if n <= window:
        return [(0, n)]

    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + window, n)
        if end < n and not content[end].isspace():
            # Back off to the last whitespace in the second half of the window.
            floor = start + window // 2
            cut = end
            while cut > floor and not content[cut - 1].isspace():
                cut -= 1
            if cut > floor:
                end = cut - 1  # drop the whitespace itself
                while end > floor and content[end - 1].isspace():
                    end -= 1
        spans.append((start, end))
        if end >= n:
            return spans
        next_start = end - overlap
        # Move forward to the start of the next word, if one starts inside the overlap.
        if not content[next_start - 1].isspace():
            space = next((i for i in range(next_start, end) if content[i].isspace()), None)
            if space is not None:
                next_start = space
        while next_start < end and content[next_start].isspace():
            next_start += 1
        start = next_start if next_start < end else end - overlap
