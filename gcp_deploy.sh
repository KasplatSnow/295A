#!/bin/bash
# ==============================================================================
# VigilZone GCP Deployment Script (Monolith)
# ==============================================================================
# Provisions a single GCP VM, generates a cloud-safe .env, uploads the repo, and
# launches the full Docker Compose stack for a quick smoke test.
#
# Prerequisites:
# 1. gcloud CLI installed and authenticated
# 2. A GCP project selected via: gcloud config set project [PROJECT_ID]
# 3. Run this script from any location; it resolves the repo automatically
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"

INSTANCE_NAME="${INSTANCE_NAME:-vigilzone-monolith}"
ZONE="${ZONE:-us-central1-a}"
REGION="${REGION:-${ZONE%-*}}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-8}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-50GB}"
BOOT_DISK_TYPE="${BOOT_DISK_TYPE:-pd-ssd}"
INSTANCE_TAG="${INSTANCE_TAG:-vigilzone-node}"
APP_FIREWALL_RULE="${APP_FIREWALL_RULE:-vigilzone-app-ports}"
SSH_FIREWALL_RULE="${SSH_FIREWALL_RULE:-vigilzone-ssh}"
SSH_SOURCE_RANGE="${SSH_SOURCE_RANGE:-0.0.0.0/0}"
APP_DIR_NAME="${APP_DIR_NAME:-vigilzone-monolith}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vigilzone}"
GIT_REPO_URL="${GIT_REPO_URL:-https://github.com/Dev228-afk/Vigilzone.git}"
GIT_BRANCH="${GIT_BRANCH:-main}"
STATIC_IP_NAME="${STATIC_IP_NAME:-${INSTANCE_NAME}-ip}"
RESERVE_STATIC_IP="${RESERVE_STATIC_IP:-1}"
CREATE_SWAP_MB="${CREATE_SWAP_MB:-8192}"
BOOTSTRAP_WAIT_RETRIES="${BOOTSTRAP_WAIT_RETRIES:-36}"
BOOTSTRAP_WAIT_SECONDS="${BOOTSTRAP_WAIT_SECONDS:-5}"
BACKEND_READY_URL="${BACKEND_READY_URL:-http://127.0.0.1:8000/admin/login/}"
AI_READY_URL="${AI_READY_URL:-http://127.0.0.1:8080/api/v1/health}"
MEDIAMTX_READY_URL="${MEDIAMTX_READY_URL:-http://127.0.0.1:9997/v3/config/global/get}"
TMP_DIR="${TMPDIR:-/tmp}"
REMOTE_ENV_FILE="vigilzone.cloud.env"
REMOTE_BOOTSTRAP_FILE="gcp.remote-bootstrap.sh"
ENV_FILE="$TMP_DIR/$REMOTE_ENV_FILE"
BOOTSTRAP_FILE="$TMP_DIR/$REMOTE_BOOTSTRAP_FILE"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/google_compute_engine}"
SSH_CONNECT_TIMEOUT_SECONDS="${SSH_CONNECT_TIMEOUT_SECONDS:-12}"
SSH_READY_RETRIES="${SSH_READY_RETRIES:-24}"

if [ -z "$PROJECT_ID" ]; then
    echo "ERROR: No GCP project is configured."
    echo "Run: gcloud config set project [PROJECT_ID]"
    exit 1
fi

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "ERROR: python3 or python is required to generate deployment secrets."
    exit 1
fi

cleanup_local_artifacts() {
    rm -f "$ENV_FILE" "$BOOTSTRAP_FILE"
}

ensure_local_ssh_key() {
    local key_dir
    key_dir="$(dirname "$SSH_KEY_FILE")"
    mkdir -p "$key_dir"

    if [ ! -f "$SSH_KEY_FILE" ] || [ ! -f "$SSH_KEY_FILE.pub" ]; then
        if ! command -v ssh-keygen >/dev/null 2>&1; then
            echo "ERROR: ssh-keygen is required to create the local GCP SSH key."
            exit 1
        fi
        echo "Creating local GCP SSH key at $SSH_KEY_FILE ..."
        ssh-keygen -t rsa -f "$SSH_KEY_FILE" -N "" -C "${USER:-vigilzone}-gcp" >/dev/null
    fi
}

gcloud_ssh_ready() {
    "$PYTHON_BIN" - "$INSTANCE_NAME" "$ZONE" "$PROJECT_ID" "$SSH_KEY_FILE" "$SSH_CONNECT_TIMEOUT_SECONDS" <<'PY'
import subprocess
import sys

instance, zone, project, key_file, timeout_seconds = sys.argv[1:6]
timeout_seconds = int(timeout_seconds)
cmd = [
    "gcloud", "compute", "ssh", instance,
    f"--zone={zone}",
    f"--project={project}",
    "--quiet",
    f"--ssh-key-file={key_file}",
    "--command=echo ready",
    "--ssh-flag=-oBatchMode=yes",
    f"--ssh-flag=-oConnectTimeout={timeout_seconds}",
    "--ssh-flag=-oStrictHostKeyChecking=no",
    "--ssh-flag=-oUserKnownHostsFile=/dev/null",
]
try:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_seconds + 10)
except subprocess.TimeoutExpired:
    sys.stderr.write("gcloud compute ssh timed out while checking SSH readiness.\n")
    sys.exit(124)

combined = (result.stdout or "") + (result.stderr or "")
if result.returncode == 0 and "ready" in (result.stdout or ""):
    sys.exit(0)

if combined.strip():
    sys.stderr.write(combined.strip() + "\n")
sys.exit(result.returncode or 1)
PY
}

maybe_reserve_static_ip() {
    if [ "$RESERVE_STATIC_IP" != "1" ]; then
        return 0
    fi

    if ! gcloud compute addresses describe "$STATIC_IP_NAME" --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "Creating static external IP: $STATIC_IP_NAME ($REGION)..."
        gcloud compute addresses create "$STATIC_IP_NAME" \
            --project="$PROJECT_ID" \
            --region="$REGION"
    fi

    local reserved_ip current_ip access_config_name
    reserved_ip="$(gcloud compute addresses describe "$STATIC_IP_NAME" --region="$REGION" --project="$PROJECT_ID" --format='get(address)')"
    current_ip="$(gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"
    access_config_name="$(gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --format='get(networkInterfaces[0].accessConfigs[0].name)')"
    access_config_name="${access_config_name:-external-nat}"

    if [ "$current_ip" != "$reserved_ip" ]; then
        if [ -n "$current_ip" ]; then
            gcloud compute instances delete-access-config "$INSTANCE_NAME" \
                --project="$PROJECT_ID" \
                --zone="$ZONE" \
                --access-config-name="$access_config_name"
        fi
        gcloud compute instances add-access-config "$INSTANCE_NAME" \
            --project="$PROJECT_ID" \
            --zone="$ZONE" \
            --access-config-name="$access_config_name" \
            --address="$reserved_ip"
    fi
}

trap cleanup_local_artifacts EXIT

echo "--- Starting VigilZone Deployment on GCP [$PROJECT_ID] ---"

echo "[1/7] Enabling required GCP APIs..."
gcloud services enable \
    serviceusage.googleapis.com \
    compute.googleapis.com \
    --project="$PROJECT_ID"

echo "[2/7] Configuring network firewall..."
if ! gcloud compute firewall-rules describe "$APP_FIREWALL_RULE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute firewall-rules create "$APP_FIREWALL_RULE" \
        --project="$PROJECT_ID" \
        --allow tcp:8085,tcp:8888,tcp:8889,udp:8189 \
        --description="VigilZone app, HLS, and WebRTC ingress" \
        --target-tags="$INSTANCE_TAG"
else
    echo "App firewall rule already exists."
fi

if ! gcloud compute firewall-rules describe "$SSH_FIREWALL_RULE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute firewall-rules create "$SSH_FIREWALL_RULE" \
        --project="$PROJECT_ID" \
        --allow tcp:22 \
        --description="VigilZone SSH ingress" \
        --source-ranges="$SSH_SOURCE_RANGE" \
        --target-tags="$INSTANCE_TAG"
else
    echo "SSH firewall rule already exists."
fi

echo "[3/7] Provisioning VM instance ($MACHINE_TYPE)..."
if ! gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute instances create "$INSTANCE_NAME" \
        --project="$PROJECT_ID" \
        --zone="$ZONE" \
        --machine-type="$MACHINE_TYPE" \
        --tags="$INSTANCE_TAG" \
        --image-family=ubuntu-2204-lts \
        --image-project=ubuntu-os-cloud \
        --boot-disk-size="$BOOT_DISK_SIZE" \
        --boot-disk-type="$BOOT_DISK_TYPE"
else
    echo "Instance already exists; ensuring it is running..."
    gcloud compute instances start "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1 || true
fi

maybe_reserve_static_ip

ensure_local_ssh_key

echo "[4/7] Waiting for SSH access..."
COUNT=0
until gcloud_ssh_ready >/dev/null 2>&1; do
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -ge "$SSH_READY_RETRIES" ]; then
        echo "ERROR: Instance did not become SSH-ready in time."
        echo "Last SSH probe output:"
        gcloud_ssh_ready || true
        exit 1
    fi
    sleep 5
    echo "Still waiting (${COUNT}x5s)..."
done

PUBLIC_IP="$(gcloud compute instances describe "$INSTANCE_NAME" --zone="$ZONE" --project="$PROJECT_ID" --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"
DJANGO_SECRET_KEY="$("$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_urlsafe(50))
PY
)"
POSTGRES_PASSWORD="$("$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"
AI_WEBHOOK_SECRET="$("$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"

echo "[5/7] Generating cloud .env for $PUBLIC_IP..."
cat > "$ENV_FILE" <<EOF
DJANGO_HOST=0.0.0.0
DJANGO_PORT=8000
AI_HOST=0.0.0.0
AI_PORT=8080
UI_PORT=8085
COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME

AI_BASE_INTERNAL=http://ai:8080
MEDIAMTX_API_URL=http://mediamtx:9997
MEDIAMTX_INTERNAL_RTSP_URL=rtsp://mediamtx:8554
MEDIAMTX_EXTERNAL_URL=http://$PUBLIC_IP:8888
MTX_WEBRTCADDITIONALHOSTS=$PUBLIC_IP

VITE_API_BASE_URL=/api
VITE_ENABLE_WEBRTC=true
VITE_WEBRTC_VIEWER_BASE_URL=http://$PUBLIC_IP:8889
VITE_HLS_VIEWER_BASE_URL=http://$PUBLIC_IP:8888
VITE_ALLOW_LOOPBACK_WEBRTC_ON_WINDOWS=false
VITE_ENABLE_MEDIAMTX_API_HEALTHCHECK=true

NGINX_BACKEND_UPSTREAM=backend:8000
NGINX_AI_UPSTREAM=ai:8080

DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY
DJANGO_DEBUG=0
ALLOWED_HOSTS=$PUBLIC_IP,localhost,127.0.0.1,backend,nginx
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8085
PUBLIC_BASE_URL=http://$PUBLIC_IP:8085
DATABASE_URL=postgresql://vigilzone:$POSTGRES_PASSWORD@postgres:5432/vigilzone
POSTGRES_DB=vigilzone
POSTGRES_USER=vigilzone
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
CHANNEL_LAYER_BACKEND=redis
REDIS_URL=redis://redis:6379/0
REDIS_HOST=redis
REDIS_PORT=6379
AI_REDIS_URL=redis://redis:6379/0
AI_REDIS_HOST=redis
AI_REDIS_PORT=6379
AI_USE_REDIS_PUBLISH=1
AI_INCIDENT_CHANNEL=vigilzone.ai.incidents
MEDIAMTX_RTSP_BASE=rtsp://mediamtx:8554
RELAY_RTSP_HOST=mediamtx
RELAY_RTSP_PORT=8554
AI_AUTO_REGISTER_WEBHOOK=0
AI_CONFIG_SNAPSHOT_MODE=backend_only
DEFAULT_AI_TENANT_ID=
STRICT_SERVICE_URL_VALIDATION=1
ALLOW_LOCALHOST_SERVICE_URLS=0

STREAM_PREVIEW_FPS=5
STREAM_PREVIEW_MAX_WIDTH=960
STREAM_PREVIEW_JPEG_QUALITY=70
STREAM_IDLE_TTL_SECONDS=60
OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp|stimeout;3000000
STREAM_PREVIEW_PREFER_AI_SNAPSHOTS=1
STREAM_PREVIEW_RTSP_FALLBACK_ENABLED=0
STREAM_PREVIEW_AI_SNAPSHOT_TIMEOUT_SECONDS=2.0
MEDIAMTX_WRITE_QUEUE_SIZE=2048

AI_API_PORT=8080
AI_PUBLIC_BASE_URL=http://ai:8080
AI_WEBHOOK_SECRET=$AI_WEBHOOK_SECRET
AI_CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8085
AI_EVIDENCE_DIR=/app/evidence
AI_DATA_DIR=/app/data
AI_STAGING_DIR=/app/data/staging_uploads
AI_ENROLL_IMAGE_DIR=/app/data/enroll_images
BACKEND_BASE_INTERNAL=http://backend:8000
BACKEND_CONFIG_SYNC_BASE=http://backend:8000/api/ai/internal
EOF

cat > "$BOOTSTRAP_FILE" <<EOF
#!/bin/bash
set -euo pipefail

BOOTSTRAP_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="\$BOOTSTRAP_DIR/$APP_DIR_NAME"
REMOTE_ENV_PATH="\$BOOTSTRAP_DIR/$REMOTE_ENV_FILE"
WAIT_RETRIES="$BOOTSTRAP_WAIT_RETRIES"
WAIT_SECONDS="$BOOTSTRAP_WAIT_SECONDS"
BACKEND_READY_URL="$BACKEND_READY_URL"
AI_READY_URL="$AI_READY_URL"
MEDIAMTX_READY_URL="$MEDIAMTX_READY_URL"
GIT_REPO_URL="$GIT_REPO_URL"
GIT_BRANCH="$GIT_BRANCH"

compose_cmd() {
    if sudo docker compose version >/dev/null 2>&1; then
        echo "sudo docker compose"
        return 0
    fi
    if command -v docker-compose >/dev/null 2>&1; then
        echo "sudo docker-compose"
        return 0
    fi
    return 1
}

wait_for_url() {
    local label="\$1"
    local url="\$2"
    local attempt=0
    while true; do
        if curl -fsS --max-time 5 "\$url" >/dev/null 2>&1; then
            return 0
        fi
        attempt=\$((attempt + 1))
        if [ "\$attempt" -ge "\$WAIT_RETRIES" ]; then
            echo "ERROR: \$label did not become ready: \$url"
            return 1
        fi
        sleep "\$WAIT_SECONDS"
    done
}

wait_for_backend_manage() {
    local attempt=0
    while true; do
        if \$COMPOSE_CMD exec -T backend python manage.py check >/dev/null 2>&1; then
            return 0
        fi
        attempt=\$((attempt + 1))
        if [ "\$attempt" -ge "\$WAIT_RETRIES" ]; then
            echo "ERROR: backend manage.py check did not succeed in time."
            return 1
        fi
        sleep "\$WAIT_SECONDS"
    done
}

wait_for_migrations() {
    local attempt=0
    while true; do
        if \$COMPOSE_CMD ps backend_migrate 2>/dev/null | grep -q "Exit 0"; then
            return 0
        fi
        attempt=\$((attempt + 1))
        if [ "\$attempt" -ge "\$WAIT_RETRIES" ]; then
            echo "ERROR: backend_migrate did not finish successfully."
            return 1
        fi
        sleep "\$WAIT_SECONDS"
    done
}

ensure_swap() {
    if [ "$CREATE_SWAP_MB" -le 0 ]; then
        return 0
    fi
    if swapon --show | grep -q '/swapfile'; then
        return 0
    fi
    sudo fallocate -l "${CREATE_SWAP_MB}M" /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count="$CREATE_SWAP_MB"
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    if ! grep -q '^/swapfile ' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    fi
    sudo sysctl vm.swappiness=10 >/dev/null
    if ! grep -q '^vm.swappiness=10' /etc/sysctl.conf; then
        echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf >/dev/null
    fi
}

emit_post_deploy_report() {
    local inspect_targets
    echo
    echo "Post-deploy diagnostics"
    echo "-----------------------"
    \$COMPOSE_CMD ps
    echo
    sudo docker stats --no-stream || true
    echo
    free -h
    echo
    df -h /
    echo
    inspect_targets="\$(\$COMPOSE_CMD ps -q ai mediamtx backend 2>/dev/null | tr '\n' ' ')"
    if [ -n "\$inspect_targets" ]; then
        sudo docker inspect \$inspect_targets --format '{{.Name}} OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || true
    fi
    echo
    if [ -n "\$(\$COMPOSE_CMD ps -q mediamtx 2>/dev/null)" ]; then
        \$COMPOSE_CMD exec -T mediamtx /mediamtx -version >/dev/null 2>&1 || true
    fi
    echo
    echo "Warnings"
    echo "- Public HTTP deploy only. Browser secure-origin features still require HTTPS."
    echo "- Static IP is expected; if disabled, restart can break WebRTC/CORS URLs."
}

run_compose_with_retries() {
    local description="\$1"
    shift
    local attempt=1
    local max_attempts=4
    while true; do
        if "\$@"; then
            return 0
        fi
        if [ "\$attempt" -ge "\$max_attempts" ]; then
            echo "ERROR: \$description failed after \$max_attempts attempts."
            return 1
        fi
        echo "Retrying \$description (\$attempt/\$max_attempts) after transient failure..."
        attempt=\$((attempt + 1))
        sleep 10
    done
}

find_compose_root() {
    local base_dir="\$1"
    local candidate=""

    if [ -f "\$base_dir/docker-compose.yml" ]; then
        echo "\$base_dir"
        return 0
    fi

    candidate="\$(find "\$base_dir" -maxdepth 2 -type f -name 'docker-compose.yml' | head -n 1)"
    if [ -n "\$candidate" ]; then
        dirname "\$candidate"
        return 0
    fi

    return 1
}

sudo apt-get update
sudo apt-get install -y docker.io git curl jq
if ! sudo apt-get install -y docker-compose-plugin; then
    sudo apt-get install -y docker-compose
fi
sudo systemctl enable docker
sudo systemctl start docker
ensure_swap

COMPOSE_CMD="\$(compose_cmd || true)"
if [ -z "\$COMPOSE_CMD" ]; then
    echo "ERROR: Docker Compose is not available after package installation."
    exit 1
fi

mkdir -p "\$APP_DIR"
find "\$APP_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
git clone --branch "\$GIT_BRANCH" --depth 1 "\$GIT_REPO_URL" "\$APP_DIR/repo"

COMPOSE_ROOT="\$(find_compose_root "\$APP_DIR/repo" || true)"
if [ -z "\$COMPOSE_ROOT" ]; then
    echo "ERROR: Could not locate docker-compose.yml after cloning the repository."
    echo "Top-level contents of \$APP_DIR:"
    find "\$APP_DIR" -maxdepth 3 -mindepth 1 | sort
    exit 1
fi

mv "\$REMOTE_ENV_PATH" "\$COMPOSE_ROOT/.env"

cd "\$COMPOSE_ROOT"
\$COMPOSE_CMD down --remove-orphans --timeout 30 || true
sudo docker container prune -f >/dev/null 2>&1 || true
sudo docker network prune -f >/dev/null 2>&1 || true
run_compose_with_retries "docker compose pull" \$COMPOSE_CMD pull --ignore-pull-failures || true
run_compose_with_retries "docker compose up" \$COMPOSE_CMD up --build -d --remove-orphans

wait_for_migrations || {
    \$COMPOSE_CMD ps
    \$COMPOSE_CMD logs --tail=200 backend_migrate postgres redis
    exit 1
}

wait_for_backend_manage || {
    \$COMPOSE_CMD ps
    \$COMPOSE_CMD logs --tail=200 backend ai nginx
    exit 1
}

wait_for_url "backend health" "\$BACKEND_READY_URL" || {
    \$COMPOSE_CMD ps
    \$COMPOSE_CMD logs --tail=200 backend nginx
    exit 1
}

wait_for_url "ai health" "\$AI_READY_URL" || {
    \$COMPOSE_CMD ps
    \$COMPOSE_CMD logs --tail=200 ai backend
    exit 1
}

wait_for_url "MediaMTX API" "\$MEDIAMTX_READY_URL" || {
    \$COMPOSE_CMD ps
    \$COMPOSE_CMD logs --tail=200 mediamtx relay_reconciler
    exit 1
}

if ! \$COMPOSE_CMD exec -T backend python manage.py migrate --check >/dev/null 2>&1; then
    echo "ERROR: unapplied migrations remain after startup."
    exit 1
fi

\$COMPOSE_CMD exec -T backend python manage.py bootstrap_postgres_config
\$COMPOSE_CMD exec -T backend python manage.py bootstrap_postgres_config

emit_post_deploy_report
EOF
chmod +x "$BOOTSTRAP_FILE"

echo "[6/7] Packaging and uploading code..."
echo "Deploy source: $GIT_REPO_URL ($GIT_BRANCH)"

gcloud compute scp "$ENV_FILE" "$BOOTSTRAP_FILE" \
    "$INSTANCE_NAME:~/" \
    --zone="$ZONE" \
    --project="$PROJECT_ID" \
    --quiet \
    --ssh-key-file="$SSH_KEY_FILE" \
    --scp-flag="-oStrictHostKeyChecking=no" \
    --scp-flag="-oUserKnownHostsFile=/dev/null"

echo "[7/7] Launching VigilZone via Docker Compose..."
gcloud compute ssh "$INSTANCE_NAME" \
    --zone="$ZONE" \
    --project="$PROJECT_ID" \
    --quiet \
    --ssh-key-file="$SSH_KEY_FILE" \
    --ssh-flag="-oStrictHostKeyChecking=no" \
    --ssh-flag="-oUserKnownHostsFile=/dev/null" \
    --command="bash ~/$REMOTE_BOOTSTRAP_FILE"

echo
echo "========================================================"
echo "Deployment completed."
echo "========================================================"
echo "Dashboard:   http://$PUBLIC_IP:8085"
echo "API Docs:    http://$PUBLIC_IP:8085/api/schema/swagger-ui/"
echo "MediaMTX HLS http://$PUBLIC_IP:8888"
echo "MediaMTX RTC http://$PUBLIC_IP:8889"
echo "--------------------------------------------------------"
echo "Next steps:"
echo "1. Verify services: gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID --command='cd ~/$APP_DIR_NAME/repo && sudo docker compose ps'"
echo "2. Create admin:    gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --project=$PROJECT_ID --command='cd ~/$APP_DIR_NAME/repo && sudo docker compose exec backend python manage.py createsuperuser'"
echo "3. Stop VM later:   bash gcp_stop.sh"
echo "========================================================"
