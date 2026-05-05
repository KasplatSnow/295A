#!/bin/bash
# ==============================================================================
# VigilZone GCP Stop Script
# ==============================================================================
# Stops the Compute Engine VM to halt CPU/RAM billing while keeping disks intact.
# ==============================================================================

set -euo pipefail

PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
INSTANCE_NAME="${INSTANCE_NAME:-vigilzone-monolith}"
ZONE="${ZONE:-us-central1-a}"

if [ -z "$PROJECT_ID" ]; then
    echo "ERROR: No GCP project is configured."
    echo "Run: gcloud config set project [PROJECT_ID]"
    exit 1
fi

if ! gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Instance '$INSTANCE_NAME' does not exist in $PROJECT_ID/$ZONE."
    exit 0
fi

STATUS="$(gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --format='get(status)')"
if [ "$STATUS" = "TERMINATED" ]; then
    echo "Instance '$INSTANCE_NAME' is already stopped."
    exit 0
fi

echo "Stopping VigilZone instance: $INSTANCE_NAME..."
gcloud compute instances stop "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID"

echo "--------------------------------------------------------"
echo "Instance stopped."
echo "You are no longer being charged for active VM compute."
echo "Run 'bash gcp_deploy.sh' to resume testing."
echo "--------------------------------------------------------"
