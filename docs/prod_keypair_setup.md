# Prod Snowflake key-pair setup — runbook

End-to-end steps to bring up `SVC_RYDER_INTEGRATION_PROD` with key-pair auth, mirroring the dev setup already done for `SVC_RYDER_INTEGRATION_DEV`.

> Dev is already complete and smoke-tested. Run these steps when you're ready to wire up prod against `MASTERY_USMMGPROD_MASTERMIND_SHARE`.

---

## Prerequisites

- Local prod key pair already generated at:
  - `C:\Users\apant\snowflake-keys\ryder_prod_private.p8`
  - `C:\Users\apant\snowflake-keys\ryder_prod_public.pub`
- The passphrase you set when generating the prod private key (kept somewhere safe — password manager, not in plaintext)
- ACCOUNTADMIN access to the Snowflake account `bj38886.central-us.azure`

---

## Step 1 — Extract the prod public-key body

In PowerShell:

```powershell
cd C:\Users\apant\snowflake-keys
(Get-Content ryder_prod_public.pub | Where-Object { $_ -notmatch '-----' }) -join '' | clip
```

Verify it landed (should print one long base64 string starting `MII...`):

```powershell
Get-Clipboard
```

If clipboard tooling is flaky, use the fallback:

```powershell
$body = (Get-Content ryder_prod_public.pub | Where-Object { $_ -notmatch '-----' }) -join ''
$body | Out-File -Encoding ascii -NoNewline pubkey_prod_body.txt
notepad pubkey_prod_body.txt
```

Then `Ctrl+A` + `Ctrl+C` from Notepad.

---

## Step 2 — Run the prod SQL in Snowflake

Open a Snowflake worksheet, sign in as ACCOUNTADMIN. Paste the public-key body into the `RSA_PUBLIC_KEY = '...'` slot, then `Ctrl+A` → Run All (or `Ctrl+Shift+Enter`).

```sql
USE ROLE ACCOUNTADMIN;

-- 1. Role
CREATE ROLE IF NOT EXISTS RYDER_INTEGRATION_ROLE_PROD
  COMMENT = 'Read-only role for the Ryder Carrier API integration (prod)';

-- 2. Warehouse usage
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE RYDER_INTEGRATION_ROLE_PROD;

-- 3. Share access — inbound Mastery TMS share for prod
GRANT IMPORTED PRIVILEGES ON DATABASE MASTERY_USMMGPROD_MASTERMIND_SHARE
  TO ROLE RYDER_INTEGRATION_ROLE_PROD;

-- 4. Service user — key-pair only, no password
CREATE USER IF NOT EXISTS SVC_RYDER_INTEGRATION_PROD
  TYPE = SERVICE
  DEFAULT_ROLE = RYDER_INTEGRATION_ROLE_PROD
  DEFAULT_WAREHOUSE = COMPUTE_WH
  DEFAULT_NAMESPACE = MASTERY_USMMGPROD_MASTERMIND_SHARE.PUBLIC
  COMMENT = 'Service account for ryder-carrier-api (prod). Key-pair auth.'
  RSA_PUBLIC_KEY = '<PASTE_PROD_PUBLIC_KEY_BODY_HERE>';

-- 5. Bind the role
GRANT ROLE RYDER_INTEGRATION_ROLE_PROD TO USER SVC_RYDER_INTEGRATION_PROD;

-- 6. Verify
DESC USER SVC_RYDER_INTEGRATION_PROD;
```

**Verification:** in the `DESC USER` results, find the row `property = RSA_PUBLIC_KEY_FP`. Its `value` should be `SHA256:<some base64>=` — a populated fingerprint. That confirms Snowflake accepted the key.

---

## Step 3 — Fill in the prod passphrase in `.env.prod`

Open `.env.prod` at the repo root and replace:

```
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=<FILL_IN_PROD_PASSPHRASE>
```

with the actual passphrase. No quotes needed unless it contains spaces, `#`, or `$`.

---

## Step 4 — Smoke test prod from your machine

Run from the `ryder-carrier-api/` directory:

```powershell
docker compose run --rm `
  --env-file .env.prod `
  -e SNOWFLAKE_PRIVATE_KEY_PATH=/keys/ryder_prod_private.p8 `
  --entrypoint python `
  app scripts/smoke_snowflake.py
```

**Expected output:**

```
auth method: keypair
user:        SVC_RYDER_INTEGRATION_PROD
account:     bj38886.central-us.azure
role:        RYDER_INTEGRATION_ROLE_PROD
database:    MASTERY_USMMGPROD_MASTERMIND_SHARE

connected: {'U': 'SVC_RYDER_INTEGRATION_PROD', 'R': 'RYDER_INTEGRATION_ROLE_PROD', 'D': 'MASTERY_USMMGPROD_MASTERMIND_SHARE'}
```

---

## Step 5 — Upload the prod secrets to Key Vault

Once the smoke test passes, push the prod secrets into Azure Key Vault so the prod Container App can read them via Managed Identity. Replace `kv-ryder-prod` with the actual prod KV name when known.

```powershell
az login   # if not already
$kv = "kv-ryder-prod"

az keyvault secret set --vault-name $kv --name snowflake-user `
  --value "SVC_RYDER_INTEGRATION_PROD"

az keyvault secret set --vault-name $kv --name snowflake-private-key `
  --file "C:\Users\apant\snowflake-keys\ryder_prod_private.p8"

az keyvault secret set --vault-name $kv --name snowflake-private-key-passphrase `
  --value "<the prod passphrase>"
```

After upload, **delete the local `ryder_prod_private.p8`** (the only copy that matters now lives in Key Vault). Keep the passphrase in your password manager — it's your only "break-glass" if the KV secret is ever lost.

---

## Likely errors and fixes

| Error | Cause | Fix |
|---|---|---|
| `Database 'MASTERY_USMMGPROD_MASTERMIND_SHARE' does not exist` | Wrong DB name or share not mounted | Run `SHOW DATABASES;` to confirm the actual name |
| `Invalid value [...] for parameter 'TYPE'` | Snowflake account too old to support `TYPE = SERVICE` | Remove the `TYPE = SERVICE` line, re-run |
| `Invalid public key` | Public-key body has stray whitespace/newlines | Re-run the PowerShell extract command, paste fresh |
| `Could not deserialize key data` (smoke test) | Wrong passphrase in `.env.prod` | Double-check the passphrase typed in `.env.prod` matches the one used when generating the key |
| `Object 'MASTERY_USMMGPROD_MASTERMIND_SHARE.PUBLIC.X' does not exist or not authorized` | Auth succeeded but the role can't see the share | Re-run the `GRANT IMPORTED PRIVILEGES` line as ACCOUNTADMIN |
