"""CLI entry point — composes the dependency graph and runs a job.

Usage:
    python -m ryder_carrier_api trace
    python -m ryder_carrier_api milestone
    python -m ryder_carrier_api cleanup

This is the *only* place that knows how to wire everything together.
Each job receives fully-constructed collaborators via constructor injection,
so individual classes stay testable in isolation.
"""

from __future__ import annotations

import argparse
import sys
from importlib.resources import files
from pathlib import Path

from dotenv import load_dotenv

from .clients.auth.base import SnowflakeAuthProvider
from .clients.auth.keypair_auth import KeyPairAuthProvider
from .clients.auth.password_auth import UsernamePasswordAuthProvider
from .clients.ryder_client import RyderClient
from .clients.snowflake_client import SnowflakeClient
from .config import AppSettings, SnowflakeAuthMethod, get_settings
from .secrets.base import SecretProvider
from .secrets.blob_json import BlobJsonSecretProvider
from .secrets.env_provider import EnvSecretProvider
from .secrets.key_vault import KeyVaultSecretProvider
from .services.cleanup_service import CleanupService
from .services.milestone_service import MilestoneService
from .services.trace_service import TraceService
from .storage.base import AuditStore, WatermarkStore
from .storage.in_memory import InMemoryAuditStore, InMemoryWatermarkStore
from .storage.table_storage import TableStorageAuditStore, TableStorageWatermarkStore
from .transformers.milestone_payload import MilestonePayloadTransformer
from .transformers.trace_payload import TracePayloadTransformer
from .utils.logging import configure_logging, get_logger


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log = get_logger(__name__).bind(job=args.job, app_env=settings.app_env.value)
    log.info("job_start")

    try:
        if args.job == "trace":
            _run_trace(settings)
        elif args.job == "milestone":
            _run_milestone(settings)
        elif args.job == "cleanup":
            _run_cleanup(settings)
        else:  # pragma: no cover — argparse already validates
            log.error("unknown_job")
            return 2
        log.info("job_complete")
        return 0
    except Exception:
        log.exception("job_failed")
        return 1


# =============================================================================
# Job runners
# =============================================================================


def _run_trace(settings: AppSettings) -> None:
    secrets = _build_secret_provider(settings)
    auth = build_auth_provider(settings, secrets)

    with (
        SnowflakeClient(settings, auth) as snowflake,
        RyderClient(settings, secrets) as ryder,
    ):
        service = TraceService(
            settings=settings,
            snowflake=snowflake,
            ryder=ryder,
            watermarks=_build_watermark_store(settings),
            audit=_build_audit_store(settings),
            transformer=TracePayloadTransformer(),
            sql=_load_sql("trace_query.sql"),
            candidates_sql=_load_sql("trace_candidates_query.sql"),
        )
        service.run()


def _run_milestone(settings: AppSettings) -> None:
    secrets = _build_secret_provider(settings)
    auth = build_auth_provider(settings, secrets)

    with (
        SnowflakeClient(settings, auth) as snowflake,
        RyderClient(settings, secrets) as ryder,
    ):
        service = MilestoneService(
            settings=settings,
            snowflake=snowflake,
            ryder=ryder,
            watermarks=_build_watermark_store(settings),
            audit=_build_audit_store(settings),
            transformer=MilestonePayloadTransformer(),
            sql=_load_sql("milestone_query.sql"),
            candidates_sql=_load_sql("milestone_candidates_query.sql"),
        )
        service.run()


def _run_cleanup(settings: AppSettings) -> None:
    audit = _build_audit_store(settings)
    CleanupService(settings=settings, audit=audit).run()


# =============================================================================
# Factories — these are the "composition root"
# =============================================================================


def _build_secret_provider(settings: AppSettings) -> SecretProvider:
    """Select SecretProvider based on which env var is set.

    Key Vault wins if both are set (more secure default). Falls back to
    EnvSecretProvider for local dev — never use that path in Azure.
    """
    if settings.key_vault_uri:
        return KeyVaultSecretProvider(vault_uri=settings.key_vault_uri)
    if settings.secrets_blob_url:
        return BlobJsonSecretProvider(blob_url=settings.secrets_blob_url)
    return EnvSecretProvider()


def build_auth_provider(settings: AppSettings, secrets: SecretProvider) -> SnowflakeAuthProvider:
    """Selects auth implementation from config. Flip method without touching code."""
    if settings.snowflake_auth_method == SnowflakeAuthMethod.PASSWORD:
        return UsernamePasswordAuthProvider(settings=settings, secrets=secrets)
    if settings.snowflake_auth_method == SnowflakeAuthMethod.KEYPAIR:
        return KeyPairAuthProvider(settings=settings, secrets=secrets)
    raise ValueError(f"Unknown SNOWFLAKE_AUTH_METHOD: {settings.snowflake_auth_method}")


def _build_watermark_store(settings: AppSettings) -> WatermarkStore:
    if not settings.storage_account_name and not settings.storage_connection_string:
        return InMemoryWatermarkStore()
    return TableStorageWatermarkStore(
        storage_account_url=settings.storage_account_url,
        table_name=settings.watermark_table_name,
        connection_string=settings.storage_connection_string or None,
    )


def _build_audit_store(settings: AppSettings) -> AuditStore:
    if not settings.storage_account_name and not settings.storage_connection_string:
        return InMemoryAuditStore()
    return TableStorageAuditStore(
        storage_account_url=settings.storage_account_url,
        table_name=settings.audit_table_name,
        connection_string=settings.storage_connection_string or None,
    )


def _load_sql(filename: str) -> str:
    """Load a SQL file from the package's installed `sql/` directory.

    Works both in development (editable install) and inside the Docker image
    where `sql/` is copied into the package directory.
    """
    # First try the installed package location (Docker image).
    try:
        return (files("ryder_carrier_api") / "sql" / filename).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass
    # Dev fallback: read from sibling `sql/` directory.
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / "sql" / filename).read_text(encoding="utf-8")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ryder_carrier_api")
    parser.add_argument(
        "job",
        choices=["trace", "milestone", "cleanup"],
        help="Which pipeline job to run.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
