#!/usr/bin/env bash
# =============================================================================
# Ryder Carrier API — APP DEPLOY
#
# 1. Reads secrets from local_config/CONFIG.<env>.json
# 2. Writes them into Key Vault (snowflake-user, snowflake-password,
#    ryder-api-key, ryder-carrier-scac)
# 3. Builds the Docker image via `az acr build` (no local Docker needed)
# 4. Updates each of the 3 Container Apps Jobs to use the new image tag
#
# Run AFTER deploy_infra.sh.
#
# Prerequisites:
#   - az CLI logged in
#   - local_config/CONFIG.<env>.json present and filled in (see CONFIG.sample.json)
#   - Infrastructure already deployed via deploy_infra.sh
#
# Usage (from repo root):
#   bash infra/deploy_app.sh dev
#   bash infra/deploy_app.sh prod
# =============================================================================

set -euo pipefail

ENV="${1:-dev}"
if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "ERROR: Environment must be 'dev' or 'prod'"
  echo "Usage: bash infra/deploy_app.sh [dev|prod]"
  exit 1
fi

# -----------------------------------------------------------------------------
# Naming — must match what deploy_infra.sh / main.bicep produce.
# -----------------------------------------------------------------------------
if [[ "$ENV" == "dev" ]]; then
  RG="DevRG"
else
  RG="rg-cus-prod-int-ryder"
fi

SUFFIX="cus-${ENV}-int-ryder"
SUFFIX_NODASH="cus${ENV}intryder"

ACR="cr${SUFFIX_NODASH}"
KV="kv-${SUFFIX}"
JOB_PREFIX="job-${SUFFIX}"
IMAGE_NAME="ryder-carrier-api"
TAG=$(git rev-parse --short HEAD 2>/dev/null || echo "manual-$(date +%Y%m%d%H%M%S)")
FULL_IMAGE="${ACR}.azurecr.io/${IMAGE_NAME}:${TAG}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
LOCAL_CONFIG="$APP_DIR/local_config"
CONFIG_FILE="$LOCAL_CONFIG/CONFIG.${ENV}.json"

echo "=== Environment    : $ENV"
echo "=== Resource group : $RG"
echo "=== ACR            : $ACR"
echo "=== Key Vault      : $KV"
echo "=== Job prefix     : $JOB_PREFIX"
echo "=== Image          : $FULL_IMAGE"

# -----------------------------------------------------------------------------
# 1. Read CONFIG.<env>.json and push secrets to Key Vault
# -----------------------------------------------------------------------------
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: Config file not found: $CONFIG_FILE"
  echo "Copy local_config/CONFIG.sample.json -> $CONFIG_FILE and fill it in."
  exit 1
fi

_get() {
  python3 -c "import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2],''); sys.stdout.write(v)" "$CONFIG_FILE" "$1"
}

SNOWFLAKE_USER=$(_get "snowflake-user")
SNOWFLAKE_PASSWORD=$(_get "snowflake-password")
RYDER_API_KEY=$(_get "ryder-api-key")
RYDER_CARRIER_SCAC=$(_get "ryder-carrier-scac")

for VAR in SNOWFLAKE_USER SNOWFLAKE_PASSWORD RYDER_API_KEY RYDER_CARRIER_SCAC; do
  if [[ -z "${!VAR}" ]]; then
    echo "ERROR: $VAR is empty in $CONFIG_FILE — fill it in before deploying."
    exit 1
  fi
done

echo ""
echo "=== Step 1: Writing secrets to Key Vault ==="
az keyvault secret set --vault-name "$KV" --name "snowflake-user"      --value "$SNOWFLAKE_USER"      --output none && echo "  set snowflake-user"
az keyvault secret set --vault-name "$KV" --name "snowflake-password"  --value "$SNOWFLAKE_PASSWORD"  --output none && echo "  set snowflake-password"
az keyvault secret set --vault-name "$KV" --name "ryder-api-key"       --value "$RYDER_API_KEY"       --output none && echo "  set ryder-api-key"
az keyvault secret set --vault-name "$KV" --name "ryder-carrier-scac"  --value "$RYDER_CARRIER_SCAC"  --output none && echo "  set ryder-carrier-scac"

# -----------------------------------------------------------------------------
# 2. Build & push image via ACR build (no local Docker required)
# -----------------------------------------------------------------------------
echo ""
echo "=== Step 2: Building & pushing image via ACR ==="
az acr build \
  --registry "$ACR" \
  --image "${IMAGE_NAME}:${TAG}" \
  --image "${IMAGE_NAME}:latest" \
  --file "$APP_DIR/Dockerfile" \
  "$APP_DIR"

echo "  Image pushed: $FULL_IMAGE"

# -----------------------------------------------------------------------------
# 3. Update each Job to use the new image
# -----------------------------------------------------------------------------
echo ""
echo "=== Step 3: Updating Container Apps Jobs ==="
for JOB in trace milestone cleanup; do
  JOB_NAME="${JOB_PREFIX}-${JOB}"
  echo "  Updating $JOB_NAME -> $FULL_IMAGE"
  az containerapp job update \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --image "$FULL_IMAGE" \
    --output none
done

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
echo ""
echo "================================================"
echo "  Deploy complete."
echo "  Image:  $FULL_IMAGE"
echo "  Jobs:   ${JOB_PREFIX}-trace, ${JOB_PREFIX}-milestone, ${JOB_PREFIX}-cleanup"
echo "  Next scheduled tick will use the new image."
echo "================================================"
