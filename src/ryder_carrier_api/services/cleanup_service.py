"""Monthly audit-table purge (rows older than retention horizon)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import AppSettings
from ..storage.base import AuditStore
from ..utils.logging import get_logger

logger = get_logger(__name__)


class CleanupService:
    def __init__(self, settings: AppSettings, audit: AuditStore) -> None:
        self._settings = settings
        self._audit = audit

    def run(self) -> int:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(
            days=self._settings.audit_retention_days
        )
        log = logger.bind(cutoff=cutoff.isoformat())
        log.info("audit_cleanup_start")
        total = 0
        for pipeline in ("trace", "milestone"):
            deleted = self._audit.delete_older_than(pipeline, cutoff)
            log.info("audit_cleanup_pipeline_done", pipeline=pipeline, deleted=deleted)
            total += deleted
        log.info("audit_cleanup_complete", deleted=total)
        return total
