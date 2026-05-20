"""Fetch ONE real row from Mastermind and show the Ryder payload it would produce.

Usage (defaults to trace; pass `milestone` for the other pipeline):
    python scripts/sample_one_row.py            # → trace
    python scripts/sample_one_row.py milestone

The database queried is whatever SNOWFLAKE_DATABASE points at — override with
docker compose to flip between dev/prod shares:
    docker compose run --rm \\
      -e SNOWFLAKE_DATABASE=MASTERY_USMMGPROD_MASTERMIND_SHARE \\
      --entrypoint python app scripts/sample_one_row.py

No data is POSTed anywhere. Read-only sample.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from dotenv import load_dotenv

from ryder_carrier_api.cli import (
    _build_secret_provider,
    _load_sql,
    build_auth_provider,
)
from ryder_carrier_api.clients.snowflake_client import SnowflakeClient
from ryder_carrier_api.config import get_settings
from ryder_carrier_api.transformers.milestone_payload import MilestonePayloadTransformer
from ryder_carrier_api.transformers.trace_payload import SkipRow, TracePayloadTransformer


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, int | float | bool):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def main() -> int:
    pipeline = sys.argv[1] if len(sys.argv) > 1 else "trace"
    if pipeline not in {"trace", "milestone"}:
        print(f"unknown pipeline: {pipeline}. use 'trace' or 'milestone'.")
        return 2

    load_dotenv()
    settings = get_settings()
    secrets = _build_secret_provider(settings)
    auth = build_auth_provider(settings, secrets)

    sql_file = "trace_query.sql" if pipeline == "trace" else "milestone_query.sql"
    sql = _load_sql(sql_file)
    transformer = (
        TracePayloadTransformer() if pipeline == "trace" else MilestonePayloadTransformer()
    )

    now = datetime.now(tz=UTC)
    params = {
        "cursor_start": now - timedelta(hours=24),
        "run_started": now,
        "customer_codes": tuple(settings.customer_codes),
    }

    print(f"pipeline:  {pipeline}")
    print(f"database:  {settings.snowflake_database}")
    print(f"user:      {secrets.get(settings.secret_name_snowflake_user)}")
    print(f"role:      {settings.snowflake_role}")
    print(f"window:    {params['cursor_start']} → {params['run_started']}")
    print(f"customers: {', '.join(settings.customer_codes)}")
    print()

    with SnowflakeClient(settings, auth) as sf:
        for row in sf.fetch_rows(sql, params=params):
            print("=" * 60)
            print("RAW ROW (from Snowflake):")
            print("=" * 60)
            print(json.dumps(_jsonable(row), indent=2, default=str))

            print()
            print("=" * 60)
            print("RYDER PAYLOAD (would be POSTed):")
            print("=" * 60)
            try:
                result = transformer.transform(row)
                print(json.dumps(result.payload, indent=2))
                print(f"\nnatural_key: {result.natural_key}")
            except SkipRow as exc:
                print(f"row would be SKIPPED: {exc}")
            return 0

    print("no rows found in the last 24h window for the configured customer codes.")
    print("try widening the window or check the customer code list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
