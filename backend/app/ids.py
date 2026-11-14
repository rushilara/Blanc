"""Prefixed ULID identifiers, e.g. ``case_01JD3X...``.

Every server-generated ID in Blanc uses ``new_id(prefix)`` so IDs are globally
unique, roughly time-sortable and self-describing (``case_``, ``analysis_``,
``board_``, ``rec_``, ...).
"""

import re

from ulid import ULID

_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]*$")

# A ULID is 26 Crockford base32 characters (no I, L, O or U).
ULID_PATTERN = r"[0-9A-HJKMNP-TV-Z]{26}"


def new_id(prefix: str) -> str:
    """Return ``f"{prefix}_{ULID}"``. ``prefix`` must be lowercase alphanumeric."""
    if not _PREFIX_RE.match(prefix):
        raise ValueError(f"invalid ID prefix {prefix!r}: use lowercase letters and digits")
    return f"{prefix}_{ULID()}"


def id_pattern(prefix: str) -> str:
    """Anchored regex matching IDs produced by ``new_id(prefix)``."""
    return rf"^{re.escape(prefix)}_{ULID_PATTERN}$"
