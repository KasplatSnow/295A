#!/bin/bash
# ==============================================================================
# VigilZone GCP Full Cleanup Script
# ==============================================================================
# Deletes the VM and firewall rules created by gcp_deploy.sh.
# ==============================================================================

set -euo pipefail

PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
INSTANCE_NAME="${INSTANCE_NAME:-vigilzone-monolith}"
ZONE="${ZONE:-us-central1-a}"
REGION="${REGION:-${ZONE%-*}}"
APP_FIREWALL_RULE="${APP_FIREWALL_RULE:-vigilzone-app-ports}"
SSH_FIREWALL_RULE="${SSH_FIREWALL_RULE:-vigilzone-ssh}"
LEGACY_FIREWALL_RULE="${LEGACY_FIREWALL_RULE:-vigilzone-ports}"
STATIC_IP_NAME="${STATIC_IP_NAME:-${INSTANCE_NAME}-ip}"

if [ -z "$PROJECT_ID" ]; then
    echo "ERROR: No GCP project is configured."
    echo "Run: gcloud config set project [PROJECT_ID]"
    exit 1
fi

echo "WARNING: This will permanently delete the VigilZone GCP smoke-test resources."
read -r -p "Proceed with cleanup? (y/N) " REPLY
if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    exit 1
fi

if gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Deleting instance: $INSTANCE_NAME..."
    gcloud compute instances delete "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --quiet
else
    echo "Instance '$INSTANCE_NAME' not found; skipping instance deletion."
fi

for RULE in "$APP_FIREWALL_RULE" "$SSH_FIREWALL_RULE" "$LEGACY_FIREWALL_RULE"; do
    if gcloud compute firewall-rules describe "$RULE" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "Deleting firewall rule: $RULE..."
        gcloud compute firewall-rules delete "$RULE" --project="$PROJECT_ID" --quiet
    else
        echo "Firewall rule '$RULE' not found; skipping."
    fi
done

if gcloud compute addresses describe "$STATIC_IP_NAME" --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Deleting static IP: $STATIC_IP_NAME..."
    gcloud compute addresses delete "$STATIC_IP_NAME" --region="$REGION" --project="$PROJECT_ID" --quiet
else
    echo "Static IP '$STATIC_IP_NAME' not found; skipping."
fi

echo "--------------------------------------------------------"
echo "Cleanup complete."
echo "All VigilZone GCP smoke-test resources have been removed."
echo "--------------------------------------------------------"
