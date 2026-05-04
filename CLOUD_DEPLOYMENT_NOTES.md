# VigilZone Cloud Deployment — 3 Plans

**Constraints**: GCP $300 credits (standalone) · AWS $100 credits (standalone) · 2-day demo window (48 hours)

---

## Services Inventory (from docker-compose.yml)

| Service | Resource Need | Latency Role |
|---|---|---|
| **AI Engine** (FastAPI+PyTorch) | 4 vCPU, 8 GB RAM (CPU) or GPU | Critical — inference |
| **Backend** (Django/Daphne) | 2 vCPU, 2 GB RAM | High — API + WebSocket |
| **relay_reconciler** | 0.5 vCPU, 512 MB | Critical — relay control plane |
| **outbox_publisher** | 0.5 vCPU, 512 MB | High — event backbone |
| **entity_embedding_worker** | 1 vCPU, 1 GB | Medium |
| **incident_subscriber** | 0.5 vCPU, 512 MB | Medium |
| **PostgreSQL** (pgvector) | 1 vCPU, 1-2 GB RAM | High — SSoT |
| **Redis** | 0.5 vCPU, 512 MB | Critical — event bus |
| **MediaMTX** | 1 vCPU, 1 GB | Critical — stream relay |
| **nginx** + React | 0.5 vCPU, 256 MB | High — edge proxy |

**Total minimum**: ~8 vCPU, 12+ GB RAM → single beefy VM running Docker Compose is still the most latency-optimal and cost-effective approach for a 2-day demo.

---

## Plan 1: GCP-Only ($300 Budget)

### Architecture: Single VM + Docker Compose

| Resource | Spec | $/hr | 48h Cost |
|---|---|---|---|
| **VM** | `e2-standard-8` (8 vCPU, 32 GB) in `us-central1-a` | $0.268 | $12.86 |
| **Boot Disk** | 50 GB `pd-ssd` | — | $0.57 |
| **Egress** | ~10 GB | — | $1.08 |
| **Snapshot** | 1× backup at 24h mark | — | $1.30 |
| **Total** | | | **~$15.81** |

**Budget used**: 5.3% of $300 · **Remaining**: $284

#### GPU Upgrade (Optional)
If real-time AI (30 FPS vs ~3 FPS CPU) matters, swap to `n1-standard-4` + 1× T4 GPU at ~$0.61/hr → **$29.28 for 48h** (still only 10% of budget). Requires GPU quota request in console.

#### Topology
```
┌─────────────────────────────────────────┐
│     GCP e2-standard-8  (Single VM)      │
│                                         │
│  nginx:8085 ─► backend:8000             │
│                   ├─► postgres:5432     │
│                   ├─► redis:6379        │
│                   └─► ai:8080           │
│  mediamtx:8554/8889/8189                │
│  relay_reconciler / outbox / subscriber │
│  entity_embedding_worker                │
│                                         │
│     docker compose up --build           │
└─────────────────────────────────────────┘
```

#### Firewall
```bash
# Only expose nginx (UI) and streaming entrypoints
gcloud compute firewall-rules create vz-http \
  --allow tcp:8085,tcp:8889,udp:8189 --source-ranges 0.0.0.0/0 --target-tags vz
gcloud compute firewall-rules create vz-ssh \
  --allow tcp:22 --source-ranges <YOUR_IP>/32 --target-tags vz
```

#### Fault Tolerance
- `restart: unless-stopped` on all containers
- GCP auto-restart VM on host failure (~60s downtime)
- Named Docker volumes for Postgres/Redis data persistence
- WAL archiving + disk snapshot for data safety
- All services on `vigilzone` bridge network → zero cross-service latency

#### Deploy Steps
```bash
# 1. Create VM
gcloud compute instances create vigilzone-demo \
  --zone=us-central1-a --machine-type=e2-standard-8 \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB --boot-disk-type=pd-ssd --tags=vz

# 2. SSH in, install Docker, clone, configure, launch
gcloud compute ssh vigilzone-demo --zone=us-central1-a
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/Dev228-afk/Vigilzone.git && cd Vigilzone
cp .env.example .env && nano .env   # Set cloud values (see Section below)
docker compose up --build -d      # backend_migrate runs automatically first
docker compose exec backend python manage.py bootstrap_postgres_config
docker compose exec backend python manage.py createsuperuser

# 3. Cleanup after demo
docker compose down -v
gcloud compute instances delete vigilzone-demo --zone=us-central1-a -q
gcloud compute firewall-rules delete vz-http vz-ssh -q
```

---

## Plan 2: AWS-Only ($100 Budget)

### Architecture: Single VM + Docker Compose

| Resource | Spec | $/hr | 48h Cost |
|---|---|---|---|
| **VM** | `t3.xlarge` (4 vCPU, 16 GB) in `us-east-1` | $0.1664 | $7.99 |
| **EBS** | 50 GB gp3 | — | $0.27 |
| **Egress** | ~10 GB (first 100 GB free/mo) | — | $0.00 |
| **Total** | | | **~$8.26** |

**Budget used**: 8.3% of $100 · **Remaining**: $91.74

> [!WARNING]
> `t3.xlarge` gives only 4 vCPU / 16 GB — tighter than the GCP plan. The AI engine will work but slower. If you need more headroom, `t3.2xlarge` (8 vCPU, 32 GB) costs $0.3328/hr → **$15.97 for 48h** (still well under $100).

> [!NOTE]
> `t3` is burstable. Enable **Unlimited mode** (default) so CPU can sustain bursts for AI inference. The extra burst cost is negligible for 48h.

#### Topology
Same as GCP — single VM running the full Docker Compose stack. All inter-service communication stays on the Docker bridge network.

#### Security Group
```bash
aws ec2 create-security-group --group-name vz-sg --description "VigilZone"
# Nginx UI
aws ec2 authorize-security-group-ingress --group-name vz-sg \
  --protocol tcp --port 8085 --cidr 0.0.0.0/0
# WebRTC streams
aws ec2 authorize-security-group-ingress --group-name vz-sg \
  --protocol tcp --port 8889 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-name vz-sg \
  --protocol udp --port 8189 --cidr 0.0.0.0/0
# SSH
aws ec2 authorize-security-group-ingress --group-name vz-sg \
  --protocol tcp --port 22 --cidr <YOUR_IP>/32
```

#### Fault Tolerance
- Same Docker restart policies as GCP plan
- EC2 auto-recovery (enable via CloudWatch alarm on `StatusCheckFailed_System`)
- EBS gp3 has 99.8-99.9% durability
- Create an EBS snapshot at 24h mark (~$0.05/GB/mo → negligible)

#### Deploy Steps
```bash
# 1. Launch EC2 (use AWS Console or CLI)
aws ec2 run-instances \
  --image-id ami-0c7217cdde317cfec \  # Ubuntu 22.04 us-east-1
  --instance-type t3.xlarge \
  --key-name <YOUR_KEY> \
  --security-group-ids <SG_ID> \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":50,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=vigilzone-demo}]'

# 2. SSH in → same Docker install + clone + compose flow as GCP

# 3. Cleanup
aws ec2 terminate-instances --instance-ids <INSTANCE_ID>
aws ec2 delete-security-group --group-name vz-sg
```

---

## Plan 3: Hybrid — GCP + AWS ($300 + $100)

### Architecture: Split Workloads Across Clouds

| Component | Cloud | VM / Service | $/hr | 48h Cost | Billed To |
|---|---|---|---|---|---|
| **AI + MediaMTX** | GCP | `e2-standard-4` (4 vCPU, 16 GB) | $0.134 | $6.43 | GCP $300 |
| GCP disk (30 GB SSD) | GCP | `pd-ssd` | — | $0.34 | GCP $300 |
| GCP egress (~5 GB) | GCP | — | — | $0.48 | GCP $300 |
| **Backend + nginx + subscriber** | AWS | `t3.medium` (2 vCPU, 4 GB) | $0.0416 | $2.00 | AWS $100 |
| **PostgreSQL** | AWS | RDS `db.t3.micro` (Free Tier) | $0.00 | $0.00 | AWS $100 |
| **Redis/Valkey** | AWS | ElastiCache `cache.t3.micro` | $0.0136 | $0.65 | AWS $100 |
| AWS disk (20 GB gp3) | AWS | EBS | — | $0.11 | AWS $100 |

| | GCP Spend | AWS Spend |
|---|---|---|
| **48h Total** | **$7.25** | **$2.76** |
| **Budget Remaining** | $292.75 | $97.24 |

#### Topology
```
         Internet
            │
   ┌────────┼────────┐
   │                  │
┌──▼──────────┐  ┌───▼────────────┐
│  AWS East   │  │  GCP Central   │
│             │  │                │
│ nginx:8085  │  │  AI Engine     │
│ backend     │◄─┤─ :8080         │
│ subscriber  │  │                │
│             │  │  MediaMTX      │
│ RDS (PG)    │  │  :8554/:8889   │
│ Valkey      │  │                │
└─────────────┘  └────────────────┘
     ▲                    ▲
     │   Public IPs +     │
     └── firewall rules ──┘
         (~25-40ms RTT)
```

> [!WARNING]
> **Cross-cloud latency**: GCP ↔ AWS adds ~25-40ms RTT. This impacts AI→Backend webhook delivery and RTSP ingest. Acceptable for a demo but noticeable vs single-VM plans.

#### Cross-Cloud Connectivity
- **Simplest**: Whitelist each VM's external IP in the other's firewall/security group. Open only needed ports (8000, 8080, 6379, 5432).
- **More secure**: Install WireGuard on both VMs for encrypted tunnel over public internet. Zero additional cost.

#### Fault Tolerance
- Each cloud independently auto-restarts its VMs
- Managed RDS handles Postgres failover/backups automatically
- Managed ElastiCache handles Redis persistence
- If GCP AI VM goes down, backend stays up (graceful degradation — no inference but API works)
- If AWS backend goes down, AI just queues events in Redis (recovers on reconnect)

---

## Cloud .env Template (All Plans)

```bash
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(50))">
ALLOWED_HOSTS=<VM_IP>,backend,nginx,localhost
CORS_ALLOWED_ORIGINS=http://<VM_IP>:8085
PUBLIC_BASE_URL=http://backend:8000
DATABASE_URL=postgresql://vigilzone:<STRONG_PW>@postgres:5432/vigilzone
POSTGRES_PASSWORD=<STRONG_PW>
REDIS_URL=redis://redis:6379/0
AI_REDIS_URL=redis://redis:6379/0
AI_BASE_INTERNAL=http://ai:8080
AI_API_PORT=8080
AI_PUBLIC_BASE_URL=http://ai:8080
AI_WEBHOOK_SECRET=<python -c "import secrets; print(secrets.token_hex(32))">
AI_USE_REDIS_PUBLISH=1
MEDIAMTX_API_URL=http://mediamtx:9997
MEDIAMTX_INTERNAL_RTSP_URL=rtsp://mediamtx:8554
MEDIAMTX_EXTERNAL_URL=http://<VM_IP>:8888
VITE_API_BASE_URL=/api
VITE_ENABLE_WEBRTC=true
VITE_WEBRTC_VIEWER_BASE_URL=http://<VM_IP>:8889
VITE_HLS_VIEWER_BASE_URL=http://<VM_IP>:8888
NGINX_BACKEND_UPSTREAM=backend:8000
NGINX_AI_UPSTREAM=ai:8080
STRICT_SERVICE_URL_VALIDATION=1
ALLOW_LOCALHOST_SERVICE_URLS=0
```

For **Hybrid (Plan 3)**, replace `redis`, `postgres`, `ai`, `mediamtx` hostnames with actual cross-cloud IPs/DNS.

---

## Side-by-Side Comparison

| | **Plan 1: GCP-Only** | **Plan 2: AWS-Only** | **Plan 3: Hybrid** |
|---|---|---|---|
| **GCP Cost** | $15.81 | $0 | $7.25 |
| **AWS Cost** | $0 | $8.26 | $2.76 |
| **Total Spend** | $15.81 | $8.26 | $10.01 |
| **Inter-service Latency** | ✅ 0ms (localhost) | ✅ 0ms (localhost) | ⚠️ 25-40ms |
| **AI Performance (CPU)** | ✅ 8 vCPU headroom | ⚠️ 4 vCPU (tighter) | ⚠️ 4 vCPU |
| **GPU Option** | ✅ T4 for $29 extra | ❌ Not on free tier | ✅ T4 on GCP side |
| **Deploy Complexity** | ✅ Simple | ✅ Simple | ⚠️ Cross-cloud networking |
| **Fault Isolation** | ❌ Single point | ❌ Single point | ✅ Separate failure domains |
| **Consistency** | ✅ ACID + local Redis | ✅ ACID + local Redis | ⚠️ Eventual (network lag) |
| **Best For** | Performance demo | Budget-conscious | Architecture showcase |

---

## Open Questions

> [!IMPORTANT]
> **1. GPU or CPU?** Real-time 30 FPS (GPU, +$29 on GCP) vs ~3 FPS (CPU, included)?

> [!IMPORTANT]
> **2. Camera source?** Test pattern, pre-recorded video via FFmpeg, or external RTSP cameras?

> [!IMPORTANT]
> **3. Domain + HTTPS?** Use `http://<IP>:8085` or set up a domain with Let's Encrypt?

> [!IMPORTANT]
> **4. Is showing hybrid (Plan 3) a requirement?** e.g., for class project multi-cloud points?
