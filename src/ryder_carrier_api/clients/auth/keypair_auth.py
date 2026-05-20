"""RSA key-pair authentication.

Select by setting `SNOWFLAKE_AUTH_METHOD=keypair`. Reads a PKCS#8
PEM-encoded private key (passphrase-protected) and converts it to the
DER bytes the Snowflake connector expects.

Two ways to source the PEM:
    1. `SNOWFLAKE_PRIVATE_KEY_PATH` env var — reads from a file on disk.
       Convenient for local dev so we don't have to embed a multi-line
       PEM in `.env`.
    2. Secret store (Key Vault / blob / env) — used when the path is
       empty. This is the prod path.

The passphrase always comes from the secret store regardless of which
PEM source is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization

from ...config import AppSettings
from ...secrets.base import SecretProvider
from .base import SnowflakeAuthProvider


class KeyPairAuthProvider(SnowflakeAuthProvider):
    def __init__(self, settings: AppSettings, secrets: SecretProvider) -> None:
        self._settings = settings
        self._secrets = secrets

    def get_connection_params(self) -> dict[str, Any]:
        if self._settings.snowflake_private_key_path:
            private_key_pem = Path(self._settings.snowflake_private_key_path).read_bytes()
        else:
            private_key_pem = self._secrets.get(
                self._settings.secret_name_snowflake_private_key
            ).encode("utf-8")
        passphrase = self._secrets.get(
            self._settings.secret_name_snowflake_private_key_passphrase
        ).encode("utf-8")

        private_key = serialization.load_pem_private_key(
            data=private_key_pem,
            password=passphrase,
        )
        private_key_der = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        return {
            "user": self._secrets.get(self._settings.secret_name_snowflake_user),
            "private_key": private_key_der,
        }
