"""Smoke test the Snowflake auth path (real DI graph, real env).

Run from the ryder-carrier-api repo root:
    python scripts/smoke_snowflake.py             # loads .env
    python scripts/smoke_snowflake.py .env.prod   # loads a specific env file

Goes through the same _build_secret_provider + build_auth_provider +
SnowflakeClient path the real jobs use, so if this succeeds the
trace/milestone jobs will too.
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

from ryder_carrier_api.cli import _build_secret_provider, build_auth_provider
from ryder_carrier_api.clients.snowflake_client import SnowflakeClient
from ryder_carrier_api.config import get_settings


def main() -> int:
    env_file = sys.argv[1] if len(sys.argv) > 1 else None
    load_dotenv(dotenv_path=env_file, override=True)
    get_settings.cache_clear()
    settings = get_settings()
    secrets = _build_secret_provider(settings)
    auth = build_auth_provider(settings, secrets)

    print(f"auth method: {settings.snowflake_auth_method.value}")
    print(f"user:        {secrets.get(settings.secret_name_snowflake_user)}")
    print(f"account:     {settings.snowflake_account}")
    print(f"role:        {settings.snowflake_role}")
    print(f"database:    {settings.snowflake_database}")
    print()

    with SnowflakeClient(settings, auth) as sf:
        rows = list(sf.fetch_rows(
            "SELECT CURRENT_USER() AS u, CURRENT_ROLE() AS r, CURRENT_DATABASE() AS d"
        ))
        print("connected:", rows[0])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
