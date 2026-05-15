"""Environment-variable SecretProvider for local development.

Reads secrets directly from environment variables (which Pydantic-Settings
loads from `.env`). Used as the fallback when neither Key Vault nor a
secrets blob is configured.

The mapping converts the logical secret name to an UPPER_SNAKE_CASE env var:
    "snowflake-user"     -> SNOWFLAKE_USER
    "snowflake-password" -> SNOWFLAKE_PASSWORD
    "ryder-api-key"      -> RYDER_API_KEY

Do not use in production — secrets in env vars don't get audited or rotated
the way Key Vault entries do. This is for local iteration only.
"""

from __future__ import annotations

import os

from .base import SecretProvider


class EnvSecretProvider(SecretProvider):
    def get(self, name: str) -> str:
        env_name = name.upper().replace("-", "_")
        value = os.getenv(env_name)
        if not value:
            raise KeyError(f"Secret '{name}' not found in environment (looked for {env_name})")
        return value
