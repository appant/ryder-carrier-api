"""Application configuration loaded from environment + Key Vault.

All swappable behaviors (auth method, customer list, retry counts) live here
so business logic stays free of environment-specific values.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEV = "dev"
    PROD = "prod"


class SnowflakeAuthMethod(str, Enum):
    PASSWORD = "password"
    KEYPAIR = "keypair"


class AppSettings(BaseSettings):
    """Settings sourced from env vars (with `.env` fallback in local dev)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Environment / observability ---
    app_env: Environment = Environment.DEV
    log_level: str = "INFO"
    application_insights_connection_string: str = ""

    # --- Secret source (pick one) ---
    # Set ONE of these. The CLI picks the matching SecretProvider at startup.
    #   key_vault_uri    -> Azure Key Vault (one secret per key, native Azure)
    #   secrets_blob_url -> Azure Blob JSON (one JSON blob holds all secrets,
    #                       matches existing telematics pattern)
    key_vault_uri: str = ""
    secrets_blob_url: str = ""

    # --- Snowflake ---
    snowflake_auth_method: SnowflakeAuthMethod = SnowflakeAuthMethod.PASSWORD
    snowflake_account: str
    snowflake_warehouse: str = "COMPUTE_WH"
    snowflake_database: str
    snowflake_schema: str = "PUBLIC"
    snowflake_role: str = ""
    snowflake_query_timeout_seconds: int = 60

    secret_name_snowflake_user: str = "snowflake-user"
    secret_name_snowflake_password: str = "snowflake-password"
    secret_name_snowflake_private_key: str = "snowflake-private-key"
    secret_name_snowflake_private_key_passphrase: str = "snowflake-private-key-passphrase"

    # --- Ryder Carrier API ---
    ryder_api_base_url: str = "https://api.ryder.com/rcsc/events/v1"
    ryder_timeout_seconds: int = 30
    ryder_max_concurrency: int = 5
    ryder_max_retries: int = 5
    secret_name_ryder_api_key: str = "ryder-api-key"
    secret_name_ryder_scac: str = "ryder-carrier-scac"

    # --- Customer filter ---
    ryder_customer_codes: str = Field(
        default="AMEBOTFRTX,DRPEPPFRTX,KEURIGFRTX,KEUDRPFRTX,MOTTSFRTX",
        description="Comma-separated list of Snowflake CUSTOMER_CODE values to include.",
    )

    # --- Storage (state) ---
    # In Azure: set storage_account_name, auth via DefaultAzureCredential.
    # Local dev (Azurite): set storage_connection_string — wins if both are set.
    storage_account_name: str = ""
    storage_connection_string: str = ""
    watermark_table_name: str = "watermarks"
    audit_table_name: str = "sentaudit"

    # --- Audit retention ---
    audit_retention_days: int = 180

    # --- Watermark safety ---
    watermark_overlap_minutes: int = 5
    watermark_max_lookback_hours: int = 72

    @property
    def customer_codes(self) -> list[str]:
        """Parsed customer codes as a list."""
        return [c.strip() for c in self.ryder_customer_codes.split(",") if c.strip()]

    @property
    def storage_account_url(self) -> str:
        return f"https://{self.storage_account_name}.table.core.windows.net"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a process-wide settings singleton."""
    return AppSettings()  # type: ignore[call-arg]
