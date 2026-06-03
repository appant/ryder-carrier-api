"""Application configuration loaded from environment + Key Vault.

All swappable behaviors (auth method, customer list, retry counts) live here
so business logic stays free of environment-specific values.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    PROD = "prod"


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
    snowflake_account: str
    snowflake_warehouse: str = "COMPUTE_WH"
    snowflake_database: str
    snowflake_schema: str = "PUBLIC"
    snowflake_role: str = ""
    snowflake_query_timeout_seconds: int = 60

    secret_name_snowflake_user: str = "snowflake-user"
    secret_name_snowflake_private_key: str = "snowflake-private-key"
    secret_name_snowflake_private_key_passphrase: str = "snowflake-private-key-passphrase"

    # When set, KeyPairAuthProvider reads the PEM from this file instead of
    # the secret store. Convenient for local dev — avoids pasting a multi-line
    # PEM into .env. Leave empty in Azure so the secret-store path is used.
    snowflake_private_key_path: str = ""

    # --- Ryder Carrier API ---
    ryder_api_base_url: str = "https://api.ryder.com/rcsc/events/v1"
    ryder_timeout_seconds: int = 30
    ryder_max_concurrency: int = 5
    ryder_max_retries: int = 5
    # Token-bucket cap on outgoing Ryder requests (requests/sec), shared across
    # worker threads. Set to ~80% of Ryder's per-SCAC limit so we self-throttle
    # proactively instead of discovering the limit via 429s. That limit is
    # enforced by Azure APIM and isn't published — confirm with Ryder, then tune
    # this. Default 8 assumes a ~10 rps limit.
    ryder_max_rps: float = 8.0
    # When Ryder returns 429/503 with a Retry-After longer than this, stop
    # retrying in-process (sleeping it would block a worker past the job
    # timeout) and let the watermark replay the row on the next scheduled run.
    ryder_retry_after_cap_seconds: int = 30
    # Bounded retry: after this many consecutive transient delivery failures for
    # the same row (across runs), give up and dead-letter it instead of stalling
    # the (shared) watermark forever. Keep small — while a row is retrying it
    # holds the watermark, so the worst-case stall is bounded by N intervals.
    ryder_max_transient_attempts: int = 3
    secret_name_ryder_api_key: str = "ryder-api-key"
    secret_name_ryder_scac: str = "ryder-carrier-scac"

    # --- Customer filter ---
    ryder_customer_codes: str = Field(
        default="ELECTRONOMI",
        description="Comma-separated list of Snowflake CUSTOMER_CODE values to include.",
    )

    # --- Storage (state) ---
    # In Azure: set storage_account_name, auth via DefaultAzureCredential.
    # Local dev (Azurite): set storage_connection_string — wins if both are set.
    storage_account_name: str = ""
    storage_connection_string: str = ""
    watermark_table_name: str = "watermarks"
    audit_table_name: str = "sentaudit"
    # Blob container holding dead-letter records (full payload + failure context
    # for rows Ryder rejected or that exhausted transient retries).
    deadletter_container_name: str = "deadletter"

    # --- Audit retention ---
    audit_retention_days: int = 180

    # --- Watermark safety ---
    # Overlap: buffer subtracted from the watermark each steady-state run so
    # late-arriving rows aren't missed.
    watermark_overlap_minutes: int = 5
    # Cold-start window: how far back the FIRST run looks when no watermark
    # exists yet. Applied on cold start ONLY — steady-state runs resume from
    # the watermark. Defaults to 1 min so a misconfigured environment won't
    # backfill old data; each environment overrides explicitly (dev=64800,
    # prod=1).
    watermark_max_lookback_minutes: int = 1
    # Catch-up cap: max minutes a steady-state run will look back once the
    # watermark has fallen behind (e.g. after a long Ryder/Snowflake outage).
    # Beyond this we clamp the cursor forward and log `puller_catchup_capped`,
    # deliberately skipping the oldest gap rather than re-querying an
    # ever-growing window (which risks the run timing out before it can even
    # dedup-scan the backlog). Default 1440 = 24h: rides out a long outage but
    # caps a pathologically stale watermark. Tune down at high volume so the
    # replay window stays well under what one run can drain.
    watermark_max_catchup_minutes: int = 1440

    # --- Diagnostic candidate-count query ---
    # Flip to False once the Ship ID remap is proven stable — avoids an extra
    # Snowflake aggregate query per run.
    enable_candidate_diagnostics: bool = True

    @property
    def customer_codes(self) -> list[str]:
        """Parsed customer codes as a list."""
        return [c.strip() for c in self.ryder_customer_codes.split(",") if c.strip()]

    @property
    def storage_account_url(self) -> str:
        return f"https://{self.storage_account_name}.table.core.windows.net"

    @property
    def storage_blob_url(self) -> str:
        return f"https://{self.storage_account_name}.blob.core.windows.net"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a process-wide settings singleton."""
    return AppSettings()  # type: ignore[call-arg]
