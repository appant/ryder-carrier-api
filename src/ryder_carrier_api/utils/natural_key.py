"""Stable natural-key hashing for dedup.

The natural key for a row is the combination of immutable identifiers that
make it unique across replays. We hash to keep the audit RowKey short and
free of characters Table Storage rejects.
"""

from __future__ import annotations

import hashlib


def natural_key_hash(*parts: str) -> str:
    """Return a hex sha256 of the parts joined by `|`.

    Use the same parts in the same order at every call site for a given
    pipeline so the hash is stable.
    """
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
