"""Ad-hoc, read-only: look up an order + its carrier by Ship ID.

    docker compose run --rm --no-deps --entrypoint python app scripts/find_shipid.py 96338985

Nothing is written or POSTed.
"""
from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

from ryder_carrier_api.cli import _build_secret_provider, build_auth_provider
from ryder_carrier_api.clients.snowflake_client import SnowflakeClient
from ryder_carrier_api.config import get_settings

SQL = """
SELECT
    oref.VALUE                AS SHIP_ID,
    o.ORDER_ID,
    o.ORDER_NUMBER,
    o.CUSTOMER_ORDER_NUMBER,
    o.LOAD_NUMBER,
    o.CUSTOMER_NAME,
    o.CUSTOMER_CODE,
    r.ROUTE_ID,
    r.CARRIER_NAME,
    r.CARRIER_CODE,
    r.CARRIER_ID
FROM ORDER_REFERENCES oref
JOIN ORDERS o  ON o.ORDER_ID = oref.ORDER_ID
LEFT JOIN ROUTES r ON r.LOAD_NUMBER = o.LOAD_NUMBER
WHERE oref.REFERENCE_TYPE = 'Ship ID'
  AND oref.VALUE = %(ship_id)s
  AND oref.IS_DELETED = FALSE
"""


def main() -> int:
    ship_id = sys.argv[1] if len(sys.argv) > 1 else "96338985"
    load_dotenv()
    settings = get_settings()
    secrets = _build_secret_provider(settings)
    auth = build_auth_provider(settings, secrets)

    print(f"database: {settings.snowflake_database}")
    print(f"ship_id:  {ship_id}\n")

    with SnowflakeClient(settings, auth) as sf:
        rows = list(sf.fetch_rows(SQL, params={"ship_id": ship_id}))

    if not rows:
        print("No order found with that Ship ID in this database.")
        return 0

    for r in rows:
        print(json.dumps(r, indent=2, default=str))
        print("-" * 50)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
