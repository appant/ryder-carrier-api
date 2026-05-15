# Ryder Carrier API Integration

Scheduled Python pipeline that pulls shipment data from Snowflake (MasterMind TMS) and pushes it to Ryder's Carrier API.

## What it does

| Job | Cadence | Purpose |
|---|---|---|
| `trace` | every 15 min | Posts GPS trace pings to `POST /loads/trace-requests` |
| `milestone` | every hour | Posts EDI214-style stop events to `POST /loads/milestone-requests` |
| `cleanup` | 1st of every month | Purges audit rows older than 180 days |

All three jobs share one Docker image; the CLI argument selects which one runs.

## Architecture

```
Snowflake (read-only) ──▶ Container App Job ──▶ Ryder Carrier API
                              │
                              ├── Key Vault (secrets)
                              └── Table Storage (watermarks + audit)
```

Resilience: watermark + inline retry + audit-table dedup. See `c:/Users/apant/.claude/plans/okay-so-we-are-flickering-stearns.md` for the full design rationale.

## Project layout

Organized by responsibility, with abstract base classes (`base.py`) under each concern and concrete implementations alongside.

```
src/ryder_carrier_api/
├── config.py                # Pydantic settings
├── cli.py                   # Entry point — composes the DI graph
├── secrets/                 # SecretProvider abstraction
├── clients/
│   ├── auth/                # SnowflakeAuthProvider abstraction
│   ├── snowflake_client.py  # Connection + paged fetch
│   └── ryder_client.py      # HTTP with retry
├── storage/                 # WatermarkStore + AuditStore abstractions
├── transformers/            # Row → Ryder JSON (pure functions)
├── services/                # Business orchestration
└── utils/                   # Timezone, logging
```

Every swappable concern (auth, secrets, storage, HTTP) is behind an interface so future implementations are config-flips, not refactors.

## Local development

Two ways to run locally: **Docker Compose** (recommended — uses Azurite for storage, mirrors prod most closely) or **bare Python on host**.

### Option A — Docker Compose (recommended)

Brings up Azurite (Azure Storage emulator) + the app container. No `az login` or real Azure storage account needed. Watermark + audit tables live in Azurite and persist across runs via the `azurite_data` volume.

#### One-time setup
```bash
cp .env.example .env             # Fill in real SNOWFLAKE_USER / SNOWFLAKE_PASSWORD / RYDER_API_KEY
```
Leave `STORAGE_CONNECTION_STRING` empty in `.env` — `docker-compose.yml` overrides it for the container.

#### Start Azurite (long-running, in the background)
```bash
docker compose up -d azurite                    # Start Azurite, detach
docker compose ps                               # Verify it's healthy
docker compose logs -f azurite                  # Tail Azurite logs
```

#### Run a job (one-shot, container exits after)
```bash
docker compose run --rm app trace               # 15-min GPS trace puller
docker compose run --rm app milestone           # 1-hour stop-event puller
docker compose run --rm app cleanup             # Audit-table purge
```

#### Rebuild the app image after code changes
```bash
docker compose build app                        # Rebuild only the app image
docker compose run --rm app trace               # Then run as usual
```

#### Simulate the 15-min cadence locally
```bash
# PowerShell (Windows):
while ($true) { docker compose run --rm app trace; Start-Sleep -Seconds 900 }

# Bash:
while true; do docker compose run --rm app trace; sleep 900; done
```

#### Tear down
```bash
docker compose down                             # Stop containers, keep Azurite data
docker compose down -v                          # Stop containers AND wipe Azurite tables (fresh slate)
```

#### Inspect Azurite tables
Use Azure Storage Explorer → "Attach with connection string" using:
```
DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEEdcAaNsCqXc8Vr+OcEs1J6Wjzr5MhrkjI2A=;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;
```
You'll see `watermarks` and `sentaudit` after the first successful run.

### Option B — Bare Python on host

Useful for fast iteration / debugging with a Python debugger attached. Still needs Azurite running (via `docker compose up -d azurite` or the Azurite VS Code extension) and `STORAGE_CONNECTION_STRING` set in `.env` to point at `http://127.0.0.1:10002/devstoreaccount1`.

#### Setup
```bash
python -m venv venv
.\venv\Scripts\activate          # Windows
pip install -r requirements.txt
pip install -e .
```

#### Run a job
```bash
python -m ryder_carrier_api trace          # Run trace puller once
python -m ryder_carrier_api milestone      # Run milestone puller once
python -m ryder_carrier_api cleanup        # Run audit cleanup
```

### Run tests
```bash
pytest
```

## Authentication

Snowflake authentication uses an abstraction so we can switch methods via the `SNOWFLAKE_AUTH_METHOD` env var without code changes:

| Value | Status | Required secrets in Key Vault |
|---|---|---|
| `password` | **Active** | `snowflake-user`, `snowflake-password` |
| `keypair` | Built but disabled | `snowflake-user`, `snowflake-private-key`, `snowflake-private-key-passphrase` |

To switch later: change env var, add new secrets to Key Vault, redeploy.

## Deployment

See `infra/` for Bicep templates. Resources provisioned:

- Container Apps Environment + 3 Jobs (trace, milestone, cleanup)
- Container Registry (Basic)
- Storage Account (Table Storage for watermarks + audit)
- Key Vault (secrets)
- User-Assigned Managed Identity
- Application Insights + Log Analytics Workspace

## Environments

| Env | Snowflake user | Mastermind share |
|---|---|---|
| dev | `SVC_RYDER_INTEGRATION_DEV` | `MASTERY_USMMGTEST_MASTERMIND_SHARE` |
| prod | `SVC_RYDER_INTEGRATION_PROD` | _TBD — production share name pending_ |

Each environment has its own resource group, Key Vault, and Storage Account. Same image, different config.
