"""15-minute GPS trace puller."""

from __future__ import annotations

from datetime import datetime

from ..clients.ryder_client import RyderEndpoint
from .base import PullerService


class TraceService(PullerService):
    pipeline_name = "trace"
    endpoint = RyderEndpoint.TRACE

    def _build_query_params(
        self, cursor_start: datetime, run_started: datetime
    ) -> dict:
        return {
            "cursor_start": cursor_start,
            "run_started": run_started,
            "customer_codes": tuple(self._settings.customer_codes),
        }
