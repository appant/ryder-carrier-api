"""Abstract base for row → Ryder JSON payload transformers.

Transformers are pure:
    - Take a single Snowflake row (dict)
    - Return a Ryder API payload (dict) + a stable natural-key string

No I/O. No state. Trivial to unit-test.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TransformedPayload:
    natural_key: str
    payload: dict[str, Any]


class PayloadTransformer(ABC):
    @abstractmethod
    def transform(self, row: dict[str, Any]) -> TransformedPayload:
        """Convert one Snowflake row into a Ryder API request body."""
