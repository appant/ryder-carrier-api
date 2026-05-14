"""Abstract interface for secret retrieval."""

from __future__ import annotations

from abc import ABC, abstractmethod


class SecretProvider(ABC):
    """Read-only secret store. Implementations: Key Vault, env-var, file, etc."""

    @abstractmethod
    def get(self, name: str) -> str:
        """Fetch the secret value for the given logical name. Raises if missing."""

    def get_optional(self, name: str, default: str | None = None) -> str | None:
        """Fetch a secret if it exists, else return the default."""
        try:
            return self.get(name)
        except KeyError:
            return default
