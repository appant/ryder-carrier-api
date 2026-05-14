"""RSA key-pair authentication.

Implemented but disabled at startup — select by setting
`SNOWFLAKE_AUTH_METHOD=keypair`. Reads a PKCS#8 PEM-encoded private key
(passphrase-protected) from Key Vault and converts to the DER bytes
that the Snowflake connector expects.

Required Key Vault secrets when this provider is active:
    snowflake-user                       Service user name
    snowflake-private-key                PEM-encoded private key
    snowflake-private-key-passphrase     Passphrase used when generating the key

This is enabled in code intentionally so we can switch with a config flag,
not a deployment. The CLI selects between providers in
`ryder_carrier_api.cli.build_auth_provider()`.
"""

from __future__ import annotations

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
