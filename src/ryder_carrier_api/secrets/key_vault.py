"""Azure Key Vault implementation of SecretProvider."""

from __future__ import annotations

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from .base import SecretProvider


class KeyVaultSecretProvider(SecretProvider):
    """Fetches secrets from Azure Key Vault using DefaultAzureCredential.

    In Azure: uses Managed Identity attached to the Container App.
    Locally:  uses `az login` credentials (run `az login` first).
    """

    def __init__(self, vault_uri: str, credential: DefaultAzureCredential | None = None) -> None:
        self._client = SecretClient(
            vault_url=vault_uri,
            credential=credential or DefaultAzureCredential(),
        )

    def get(self, name: str) -> str:
        try:
            secret = self._client.get_secret(name)
        except ResourceNotFoundError as exc:
            raise KeyError(f"Secret '{name}' not found in Key Vault") from exc
        if secret.value is None:
            raise KeyError(f"Secret '{name}' has no value")
        return secret.value
