#!/bin/bash
# ==============================================================================
# VigilZone GCP Full Cleanup Script
# ==============================================================================
# Deletes the VM and Firewall rules to completely remove all resources.
# ==============================================================================

INSTANCE_NAME="vigilzone-monolith"
ZONE="us-central1-a"
FIREWALL_RULE="vigilzone-ports"

echo "⚠️ WARNING: This will PERMANENTLY DELETE the instance and all data."
read -p "Are you sure you want to proceed? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    exit 1
fi

echo "Deleting instance: $INSTANCE_NAME..."
gcloud compute instances delete $INSTANCE_NAME --zone=$ZONE --quiet

echo "Deleting firewall rule: $FIREWALL_RULE..."
gcloud compute firewall-rules delete $FIREWALL_RULE --quiet

echo "--------------------------------------------------------"
echo "✅ CLEANUP COMPLETE."
echo "All VigilZone GCP resources have been removed."
echo "--------------------------------------------------------"
