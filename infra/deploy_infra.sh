#!/usr/bin/env bash
# =============================================================================
# Ryder Carrier API — INFRA DEPLOY
#
# Provisions all Azure resources via Bicep:
#   - ACR, Storage (watermarks + sentaudit tables), Key Vault
#   - User-Assigned Managed Identity (UAMI) with role assignments:
#       * AcrPull on ACR
#       * Key Vault Secrets User on KV
#       * Storage Table Data Contributor on storage
#   - Log Analytics Workspace + Application Insights
#   - Container Apps Environment
#   - 3 Container Apps Jobs (trace/milestone/cleanup) with cron triggers,
#     pointing at a placeholder image
#
# Run BEFORE deploy_app.sh.
#
# Prerequisites:
#   - az CLI logged in (az login)
#   - The resource group must already exist (DevRG for dev, rg-cus-prod-int-ryder for prod)
#
# Usage:
#   bash infra/deploy_infra.sh dev
#   bash infra/deploy_infra.sh prod
# =============================================================================

set -euo pipefail

ENV="${1:-dev}"
if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "ERROR: Environment must be 'dev' or 'prod'"
  echo "Usage: bash infra/deploy_infra.sh [dev|prod]"
  exit 1
fi

if [[ "$ENV" == "dev" ]]; then
  RG="DevRG"
else
  RG="rg-cus-prod-int-ryder"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Environment   : $ENV"
echo "=== Resource group: $RG"
echo "=== Template      : $SCRIPT_DIR/main.bicep"
echo "=== Parameters    : $SCRIPT_DIR/${ENV}.bicepparam"

# ── Resource group must exist ────────────────────────────────────────────────
if ! az group show --name "$RG" >/dev/null 2>&1; then
  echo "ERROR: Resource group '$RG' does not exist. Create it manually first:"
  echo "       az group create --name $RG --location centralus"
  exit 1
fi

# ── Deploy ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Running Bicep what-if (preview of changes) ==="
az deployment group what-if \
  --resource-group "$RG" \
  --template-file  "$SCRIPT_DIR/main.bicep" \
  --parameters     "$SCRIPT_DIR/${ENV}.bicepparam"

echo ""
read -r -p "Proceed with the deployment? [y/N] " CONFIRM
if [[ "${CONFIRM,,}" != "y" ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
echo "=== Deploying Bicep ==="
az deployment group create \
  --resource-group "$RG" \
  --template-file  "$SCRIPT_DIR/main.bicep" \
  --parameters     "$SCRIPT_DIR/${ENV}.bicepparam" \
  --mode           Incremental \
  --query          "properties.outputs" \
  --output         table

echo ""
echo "=== Infrastructure deployed."
echo "=== Next steps:"
echo "    1. Populate Key Vault with secrets (see deploy_app.sh — it does this for you)."
echo "    2. Run: bash infra/deploy_app.sh $ENV"
