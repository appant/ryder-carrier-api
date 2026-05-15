"""Azure Blob Storage JSON-based SecretProvider.

Reads a single JSON blob containing all secrets as a flat key/value object,
matching the existing telematics pattern (lynx_config.json,
thermoking_config.json). The blob is downloaded once at startup and cached
in memory; subsequent get() calls are dict lookups.

Authenticates via DefaultAzureCredential — same pattern as KeyVaultSecretProvider.
"""

from __future__ import annotations

import json

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobClient

from .base import SecretProvider


class BlobJsonSecretProvider(SecretProvider):
    """Downloads a JSON config blob once on init and serves keys from it.

    Expected blob shape:
        {
          "snowflake-user": "SVC_RYDER_INTEGRATION_DEV",
          "snowflake-password": "...",
          "ryder-api-key": "...",
          "ryder-carrier-scac": "USMM"
        }

    Secret names must match the same logical names used elsewhere
    (e.g. those configured in config.py: `secret_name_snowflake_user`).
    """

    def __init__(self, blob_url: str, credential: DefaultAzureCredential | None = None) -> None:
        client = BlobClient.from_blob_url(
            blob_url=blob_url,
            credential=credential or DefaultAzureCredential(),
        )
        try:
            payload = client.download_blob().readall()
        except ResourceNotFoundError as exc:
            raise KeyError(f"Secrets blob not found: {blob_url}") from exc
        self._config: dict[str, str] = json.loads(payload)

    def get(self, name: str) -> str:
        if name not in self._config:
            raise KeyError(f"Secret '{name}' not in blob config")
        value = self._config[name]
        if value is None or value == "":
            raise KeyError(f"Secret '{name}' is empty in blob config")
        return str(value)
