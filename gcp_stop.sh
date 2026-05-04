#!/bin/bash
# ==============================================================================
# VigilZone GCP Stop Script
# ==============================================================================
# Immediately stops the Compute Engine instance to halt CPU/RAM billing.
# Disk storage (minimal cost) will still persist.
# ==============================================================================

INSTANCE_NAME="vigilzone-monolith"
ZONE="us-central1-a"

echo "Stopping VigilZone instance: $INSTANCE_NAME..."
gcloud compute instances stop $INSTANCE_NAME --zone=$ZONE

echo "--------------------------------------------------------"
echo "✅ Instance STOPPED."
echo "You are no longer being charged for Compute credits."
echo "Run 'bash gcp_deploy.sh' to resume testing."
echo "--------------------------------------------------------"
