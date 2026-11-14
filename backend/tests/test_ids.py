import re

import pytest

from app.ids import id_pattern, new_id


def test_new_id_has_prefix_and_ulid() -> None:
    value = new_id("case")
    assert re.match(id_pattern("case"), value)
    assert len(value) == len("case_") + 26


def test_new_id_is_unique() -> None:
    ids = {new_id("rec") for _ in range(10_000)}
    assert len(ids) == 10_000


@pytest.mark.parametrize("prefix", ["", "Case", "case_", "1case", "ca-se"])
def test_new_id_rejects_bad_prefix(prefix: str) -> None:
    with pytest.raises(ValueError):
        new_id(prefix)
