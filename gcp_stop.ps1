# VigilZone GCP Stop Script (PowerShell)
$INSTANCE_NAME = "vigilzone-monolith"
$ZONE = "us-central1-a"

Write-Host "Stopping VigilZone instance: $INSTANCE_NAME..."
gcloud compute instances stop $INSTANCE_NAME --zone=$ZONE

Write-Host "`n✅ Instance STOPPED." -ForegroundColor Green
Write-Host "You are no longer being charged for Compute credits."
