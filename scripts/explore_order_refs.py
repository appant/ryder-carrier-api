"""Ad-hoc, read-only: find which ORDER_REFERENCES.REFERENCE_TYPE holds the Ship ID.

REFERENCE_TYPE is a client-defined label, so we list what this tenant actually
uses and sample a few rows. Nothing is written or POSTed.

    docker compose run --rm --no-deps --entrypoint python app scripts/explore_order_refs.py
"""
from __future__ import annotations

import json

from dotenv import load_dotenv

from ryder_carrier_api.cli import _build_secret_provider, build_auth_provider
from ryder_carrier_api.clients.snowflake_client import SnowflakeClient
from ryder_carrier_api.config import get_settings


def main() -> int:
    load_dotenv()
    settings = get_settings()
    secrets = _build_secret_provider(settings)
    auth = build_auth_provider(settings, secrets)

    with SnowflakeClient(settings, auth) as sf:
        print(f"database: {settings.snowflake_database}\n")

        print("=== distinct REFERENCE_TYPE values (with counts) ===")
        for r in sf.fetch_rows(
            "SELECT REFERENCE_TYPE, COUNT(*) AS N "
            "FROM ORDER_REFERENCES WHERE IS_DELETED = FALSE "
            "GROUP BY REFERENCE_TYPE ORDER BY N DESC"
        ):
            print(f"  {r}")

        print("\n=== sample rows (any type that looks like a ship id) ===")
        for r in sf.fetch_rows(
            "SELECT ORDER_ID, ORDER_NUMBER, REFERENCE_TYPE, VALUE, EDI_QUALIFIER "
            "FROM ORDER_REFERENCES WHERE IS_DELETED = FALSE "
            "AND REFERENCE_TYPE ILIKE '%ship%' LIMIT 10"
        ):
            print(f"  {json.dumps(r, default=str)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
