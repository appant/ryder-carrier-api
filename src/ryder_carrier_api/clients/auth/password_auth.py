"""Username + password authentication (active method).

Active initially to match existing telematics pipelines and ship faster.
Will be replaced by KeyPairAuthProvider by flipping `SNOWFLAKE_AUTH_METHOD=keypair`.
"""

from __future__ import annotations

from typing import Any

from ...config import AppSettings
from ...secrets.base import SecretProvider
from .base import SnowflakeAuthProvider


class UsernamePasswordAuthProvider(SnowflakeAuthProvider):
    def __init__(self, settings: AppSettings, secrets: SecretProvider) -> None:
        self._settings = settings
        self._secrets = secrets

    def get_connection_params(self) -> dict[str, Any]:
        return {
            "user": self._secrets.get(self._settings.secret_name_snowflake_user),
            "password": self._secrets.get(self._settings.secret_name_snowflake_password),
        }
