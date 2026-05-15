"""Snowflake client — owns connection lifecycle and paged result streaming.

Knows nothing about *how* it's authenticated. The SnowflakeAuthProvider
returns connection params; this client just merges them with the static
settings (account, warehouse, db, role) and opens the connection.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any

import snowflake.connector

from ..config import AppSettings
from .auth.base import SnowflakeAuthProvider


class SnowflakeClient:
    """Thin wrapper that issues queries and streams paged results.

    Use as a context manager so the connection is always closed cleanly.
    """

    def __init__(self, settings: AppSettings, auth: SnowflakeAuthProvider) -> None:
        self._settings = settings
        self._auth = auth
        self._connection: snowflake.connector.SnowflakeConnection | None = None

    # --- lifecycle ---

    def __enter__(self) -> SnowflakeClient:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def connect(self) -> None:
        if self._connection is not None:
            return
        params: dict[str, Any] = {
            "account": self._settings.snowflake_account,
            "warehouse": self._settings.snowflake_warehouse,
            "database": self._settings.snowflake_database,
            "schema": self._settings.snowflake_schema,
            "client_session_keep_alive": False,
            "network_timeout": self._settings.snowflake_query_timeout_seconds,
            **self._auth.get_connection_params(),
        }
        if self._settings.snowflake_role:
            params["role"] = self._settings.snowflake_role
        self._connection = snowflake.connector.connect(**params)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # --- queries ---

    @contextmanager
    def _cursor(self) -> Generator[Any, None, None]:
        if self._connection is None:
            raise RuntimeError("SnowflakeClient is not connected; use within a `with` block.")
        cur = self._connection.cursor(snowflake.connector.DictCursor)
        try:
            yield cur
        finally:
            cur.close()

    def fetch_rows(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        chunk_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Execute a query and yield rows one at a time.

        Streams in chunks via the cursor's fetchmany() so we don't hold the
        whole result set in memory. Critical for catch-up scenarios where a
        single run might pull tens of thousands of rows.
        """
        with self._cursor() as cur:
            cur.execute(sql, params or {})
            while True:
                batch = cur.fetchmany(chunk_size)
                if not batch:
                    break
                yield from batch
