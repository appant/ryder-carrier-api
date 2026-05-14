"""Abstract interface for Snowflake authentication.

The provider returns connection parameters; the Snowflake client doesn't know
or care whether they came from a password, a private key, or anything else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SnowflakeAuthProvider(ABC):
    """Returns connection parameters consumable by snowflake.connector.connect()."""

    @abstractmethod
    def get_connection_params(self) -> dict[str, Any]:
        """Return kwargs to pass to snowflake.connector.connect()."""
