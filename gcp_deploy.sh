#!/bin/bash
# ==============================================================================
# VigilZone GCP Deployment Script (Monolith)
# ==============================================================================
# This script provisions an e2-standard-2 VM on Google Cloud, installs Docker,
# uploads the current repository, and starts the full VigilZone stack.
#
# Prerequisites:
# 1. gcloud CLI installed and authenticated (gcloud auth login)
# 2. Project ID set (gcloud config set project [YOUR_PROJECT_ID])
# ==============================================================================

set -e # Exit on error

# Configuration
PROJECT_ID=$(gcloud config get-value project)
INSTANCE_NAME="vigilzone-monolith"
ZONE="us-central1-a"
MACHINE_TYPE="e2-standard-2" # 2 vCPU, 8GB RAM (Fits well within $300 credit)
FIREWALL_RULE="vigilzone-ports"

if [ -z "$PROJECT_ID" ]; then
    echo "ERROR: No GCP Project ID found. Please run: gcloud config set project [PROJECT_ID]"
    exit 1
fi

echo "--- Starting VigilZone Deployment on GCP [$PROJECT_ID] ---"

# 1. Create Firewall Rule (if not exists)
echo "[1/5] Configuring Network Firewall..."
if ! gcloud compute firewall-rules describe $FIREWALL_RULE >/dev/null 2>&1; then
    gcloud compute firewall-rules create $FIREWALL_RULE \
        --allow tcp:8085,tcp:8000,tcp:8554,tcp:8888,tcp:8889,tcp:1935,tcp:9997 \
        --description="VigilZone Monolith Ports" \
        --target-tags=vigilzone-node
else
    echo "Firewall rule already exists."
fi

# 2. Provision Compute Engine Instance
echo "[2/5] Provisioning VM Instance ($MACHINE_TYPE)..."
if ! gcloud compute instances describe $INSTANCE_NAME --zone=$ZONE >/dev/null 2>&1; then
    gcloud compute instances create $INSTANCE_NAME \
        --zone=$ZONE \
        --machine-type=$MACHINE_TYPE \
        --tags=vigilzone-node \
        --image-family=ubuntu-2204-lts \
        --image-project=ubuntu-os-cloud \
        --boot-disk-size=30GB \
        --metadata=startup-script="#!/bin/bash
            apt-get update
            apt-get install -y docker.io docker-compose git
            systemctl start docker
            systemctl enable docker
        "
else
    echo "Instance already exists, starting it..."
    gcloud compute instances start $INSTANCE_NAME --zone=$ZONE
fi

# 3. Wait for initialization
echo "[3/5] Waiting for instance to initialize (this takes ~45s)..."
MAX_RETRIES=30
COUNT=0
until gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command="docker --version" >/dev/null 2>&1 || [ $COUNT -eq $MAX_RETRIES ]; do
    sleep 5
    COUNT=$((COUNT + 1))
    echo "Still waiting ($((COUNT * 5))s)..."
done

# 4. Upload Files (Excluding heavy/local directories)
echo "[4/5] Uploading codebase (excluding local artifacts)..."
# We create a temporary archive to speed up transfer and exclude junk
TAR_FILE="vigilzone_deploy.tar.gz"
tar --exclude='node_modules' --exclude='.git' --exclude='graphify-out' --exclude='__pycache__' -czf $TAR_FILE .

gcloud compute scp $TAR_FILE $INSTANCE_NAME:~/ --zone=$ZONE
gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command="mkdir -p ~/vigilzone && tar -xzf ~/$TAR_FILE -C ~/vigilzone && rm ~/$TAR_FILE"
rm $TAR_FILE

# 5. Start Docker Stack
echo "[5/5] Launching VigilZone via Docker Compose..."
# We pass the public IP to the environment so MediaMTX/Frontend can be reached correctly
PUBLIC_IP=$(gcloud compute instances describe $INSTANCE_NAME --zone=$ZONE --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command="
    cd ~/vigilzone
    # Create a production-ready .env if not exists
    if [ ! -f .env ]; then
        cp .env.example .env
    fi
    # Update allowed hosts/origins to the public IP
    sed -i 's/ALLOWED_HOSTS=.*/ALLOWED_HOSTS=$PUBLIC_IP,localhost,127.0.0.1,backend,nginx/g' .env
    sed -i 's|VITE_API_BASE_URL=.*|VITE_API_BASE_URL=http://$PUBLIC_IP:8085/api|g' .env
    
    sudo docker-compose up -d
"

echo ""
echo "========================================================"
echo "🚀 DEPLOYMENT SUCCESSFUL!"
echo "========================================================"
echo "UI Dashboard: http://$PUBLIC_IP:8085"
echo "Backend API:  http://$PUBLIC_IP:8000/api/schema/swagger-ui/"
echo "MediaMTX UI:  http://$PUBLIC_IP:9997"
echo "--------------------------------------------------------"
echo "Use 'bash gcp_stop.sh' to halt instance and save costs."
echo "========================================================"
