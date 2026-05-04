# VigilZone GCP Deployment Script (PowerShell)
$PROJECT_ID = gcloud config get-value project
$INSTANCE_NAME = "vigilzone-monolith"
$ZONE = "us-central1-a"
$MACHINE_TYPE = "e2-standard-2"
$FIREWALL_RULE = "vigilzone-ports"

if (-not $PROJECT_ID) {
    Write-Error "No GCP Project ID found. Run: gcloud config set project [PROJECT_ID]"
    exit
}

Write-Host "--- Starting VigilZone Deployment on GCP [$PROJECT_ID] ---"

# 1. Firewall
Write-Host "[1/5] Configuring Network Firewall..."
$fw = gcloud compute firewall-rules list --filter="name=$FIREWALL_RULE" --format="value(name)"
if (-not $fw) {
    gcloud compute firewall-rules create $FIREWALL_RULE `
        --allow tcp:8085,tcp:8000,tcp:8554,tcp:8888,tcp:8889,tcp:1935,tcp:9997 `
        --description="VigilZone Monolith Ports" `
        --target-tags=vigilzone-node
}

# 2. Instance
Write-Host "[2/5] Provisioning VM Instance ($MACHINE_TYPE)..."
$inst = gcloud compute instances list --filter="name=$INSTANCE_NAME" --format="value(name)"
if (-not $inst) {
    gcloud compute instances create $INSTANCE_NAME `
        --zone=$ZONE `
        --machine-type=$MACHINE_TYPE `
        --tags=vigilzone-node `
        --image-family=ubuntu-2204-lts `
        --image-project=ubuntu-os-cloud `
        --boot-disk-size=30GB `
        --metadata=startup-script="#!/bin/bash
            apt-get update
            apt-get install -y docker.io docker-compose git
            systemctl start docker
            systemctl enable docker
        "
} else {
    gcloud compute instances start $INSTANCE_NAME --zone=$ZONE
}

# 3. Wait
Write-Host "[3/5] Waiting for instance to initialize..."
Start-Sleep -Seconds 45

# 4. Upload (Simplified for PS)
Write-Host "[4/5] Uploading codebase..."
# Zip is easier in PS for transfer
$ZipFile = "vigilzone_deploy.zip"
if (Test-Path $ZipFile) { Remove-Item $ZipFile }
Compress-Archive -Path "services", "web", "docker-compose.yml", ".env.example" -DestinationPath $ZipFile

gcloud compute scp $ZipFile "$INSTANCE_NAME`:~/" --zone=$ZONE
gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command="sudo apt-get install -y unzip && unzip -o ~/$ZipFile -d ~/vigilzone && rm ~/$ZipFile"
Remove-Item $ZipFile

# 5. Start
Write-Host "[5/5] Launching VigilZone..."
$PUBLIC_IP = gcloud compute instances describe $INSTANCE_NAME --zone=$ZONE --format='get(networkInterfaces[0].accessConfigs[0].natIP)'

gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command="
    cd ~/vigilzone
    if [ ! -f .env ]; then cp .env.example .env; fi
    sed -i 's/ALLOWED_HOSTS=.*/ALLOWED_HOSTS=$PUBLIC_IP,localhost,127.0.0.1,backend,nginx/g' .env
    sudo docker-compose up -d
"

Write-Host "`n🚀 DEPLOYMENT SUCCESSFUL!" -ForegroundColor Green
Write-Host "UI Dashboard: http://$PUBLIC_IP:8085"
Write-Host "Backend API:  http://$PUBLIC_IP:8000/api/schema/swagger-ui/"
Write-Host "MediaMTX UI:  http://$PUBLIC_IP:9997"
