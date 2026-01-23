# Kanyo Docker Deployment Guide

This guide covers deploying Kanyo with Docker, from single-stream to multi-stream production setups.

---

## Quick Start (Single Stream)

### Prerequisites

- Docker and Docker Compose
- NVIDIA GPU with drivers (or see [CPU/Intel variants](#hardware-variants))
- NVIDIA Container Toolkit installed

### Step 1: Create Deployment Directory

```bash
mkdir -p /opt/services/kanyo-mystream
cd /opt/services/kanyo-mystream
```

### Step 2: Get Docker Compose File

```bash
curl -O https://raw.githubusercontent.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/main/docker/docker-compose.yml
```

### Step 3: Create Configuration

```bash
curl -O https://raw.githubusercontent.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/main/configs/config.template.yaml
mv config.template.yaml config.yaml
```

Edit `config.yaml`:
```yaml
video_source: "https://www.youtube.com/watch?v=YOUR_VIDEO_ID"
timezone: "America/New_York"
stream_name: "My Falcon Cam"
telegram_enabled: false  # Set to true after configuring Telegram
```

### Step 4: Create Directories and Environment

```bash
mkdir -p clips logs

# If using Telegram notifications:
echo "TELEGRAM_BOT_TOKEN=your_token_here" > .env
```

### Step 5: Start

```bash
docker compose up -d
```

### Step 6: Verify

```bash
docker logs kanyo-detection --tail 50 -f
```

You should see:
```
INFO | ✅ Connected to stream
INFO | Frame 100: bird detected (confidence: 0.67)
```

---

## Hardware Variants

Three Docker images are available:

| Image Tag | Hardware | Use Case |
|-----------|----------|----------|
| `:nvidia` | NVIDIA GPU | Fastest, recommended |
| `:vaapi` | Intel iGPU | Good for Intel systems |
| `:cpu` | CPU only | Works anywhere, slower |

To use a different variant, change the image in `docker-compose.yml`:

```yaml
x-kanyo-gpu-service: &kanyo-gpu-service
  image: ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:vaapi  # or :cpu
```

For Intel iGPU, also remove the NVIDIA `deploy` section and add device access:

```yaml
x-kanyo-service: &kanyo-service
  image: ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:vaapi
  devices:
    - /dev/dri:/dev/dri
  # ... rest of config (no deploy.resources.reservations)
```

For CPU-only, remove the entire `deploy` section.

---

## Multi-Stream Deployment

For monitoring multiple camera streams, use the YAML anchor pattern.

### Directory Structure

```
/opt/services/
├── kanyo-admin/              # Docker Compose, .env
│   ├── docker-compose.yml
│   └── .env
├── kanyo-harvard/            # Stream 1
│   ├── config.yaml
│   ├── clips/
│   └── logs/
├── kanyo-nsw/                # Stream 2
│   ├── config.yaml
│   ├── clips/
│   └── logs/
└── kanyo-code/               # Source code (for development)
    └── src/
```

### Multi-Stream docker-compose.yml

```yaml
x-kanyo-gpu-service: &kanyo-gpu-service
  image: ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:nvidia
  pull_policy: if_not_present
  env_file: .env
  shm_size: '2gb'
  environment:
    - PYTHONUNBUFFERED=1
    - MALLOC_ARENA_MAX=2
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  restart: unless-stopped
  logging:
    driver: "json-file"
    options:
      max-size: "10m"
      max-file: "3"

services:
  harvard-gpu:
    <<: *kanyo-gpu-service
    container_name: kanyo-harvard-gpu
    command: ["/bin/sh", "-c", "umask 027 && exec python -m kanyo.detection.buffer_monitor"]
    volumes:
      - ${KANYO_CAM1_ROOT}/config.yaml:/app/config.yaml:ro
      - ${KANYO_CAM1_ROOT}/clips:/app/clips
      - ${KANYO_CAM1_ROOT}/logs:/app/logs

  nsw-gpu:
    <<: *kanyo-gpu-service
    container_name: kanyo-nsw-gpu
    command: ["/bin/sh", "-c", "umask 027 && exec python -m kanyo.detection.buffer_monitor"]
    volumes:
      - ${KANYO_CAM2_ROOT}/config.yaml:/app/config.yaml:ro
      - ${KANYO_CAM2_ROOT}/clips:/app/clips
      - ${KANYO_CAM2_ROOT}/logs:/app/logs
```

### Environment File (.env)

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
KANYO_CAM1_ROOT=/opt/services/kanyo-harvard
KANYO_CAM2_ROOT=/opt/services/kanyo-nsw
```

### Adding a New Stream

1. Create stream directory:
   ```bash
   mkdir -p /opt/services/kanyo-newstream/{clips,logs}
   ```

2. Create `config.yaml` for the stream

3. Add to `.env`:
   ```bash
   KANYO_CAM3_ROOT=/opt/services/kanyo-newstream
   ```

4. Add service to `docker-compose.yml`:
   ```yaml
   newstream-gpu:
     <<: *kanyo-gpu-service
     container_name: kanyo-newstream-gpu
     command: ["/bin/sh", "-c", "umask 027 && exec python -m kanyo.detection.buffer_monitor"]
     volumes:
       - ${KANYO_CAM3_ROOT}/config.yaml:/app/config.yaml:ro
       - ${KANYO_CAM3_ROOT}/clips:/app/clips
       - ${KANYO_CAM3_ROOT}/logs:/app/logs
   ```

5. Start:
   ```bash
   docker compose up -d newstream-gpu
   ```

---

## Development Workflow

For rapid iteration during development, mount source code from the host instead of using the code baked into the image.

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git /opt/services/kanyo-code
   ```

2. Add code volume to your services:
   ```yaml
   services:
     harvard-gpu:
       <<: *kanyo-gpu-service
       volumes:
         - /opt/services/kanyo-code/src:/app/src:ro    # ADD THIS
         - ${KANYO_CAM1_ROOT}/config.yaml:/app/config.yaml:ro
         - ${KANYO_CAM1_ROOT}/clips:/app/clips
         - ${KANYO_CAM1_ROOT}/logs:/app/logs
   ```

### Update Cycle

With volume-mounted code, updates are instant:

```bash
# On your development machine
git add -A && git commit -m "Fix bug" && git push

# On the server
cd /opt/services/kanyo-code && git pull
cd /opt/services/kanyo-admin && docker compose restart
```

Total time: ~10 seconds vs ~45 minutes for image rebuilds.

### When to Rebuild Images

Only rebuild images when:
- `requirements.txt` changes
- Dockerfile changes
- Creating a production release

```bash
# Rebuild locally
docker build -f docker/Dockerfile.nvidia -t kanyo:nvidia .

# Or pull updated image
docker compose pull
```

---

## ZFS Storage (Optional)

For production deployments, ZFS provides snapshots, compression, and quotas.

### Create Datasets

```bash
# Parent dataset
sudo zfs create rpool/kanyo

# Per-stream datasets
sudo zfs create rpool/kanyo/harvard
sudo zfs create rpool/kanyo/nsw

# Set quotas (optional)
sudo zfs set quota=500G rpool/kanyo/harvard
sudo zfs set quota=500G rpool/kanyo/nsw

# Enable compression
sudo zfs set compression=lz4 rpool/kanyo
```

### Snapshots

```bash
# Manual snapshot
sudo zfs snapshot -r rpool/kanyo@backup-$(date +%Y%m%d)

# Automated daily snapshots (add to crontab)
0 2 * * * /usr/sbin/zfs snapshot -r rpool/kanyo@daily-$(date +\%Y\%m\%d)

# Keep last 7 days
0 3 * * * /usr/sbin/zfs list -t snapshot -o name | grep 'daily' | head -n -7 | xargs -n1 /usr/sbin/zfs destroy
```

### Restore

```bash
# List snapshots
zfs list -t snapshot | grep kanyo

# Rollback (destructive)
sudo zfs rollback rpool/kanyo/harvard@daily-20260123

# Or restore specific files
cp /opt/services/kanyo-harvard/.zfs/snapshot/daily-20260123/clips/2026-01-22/* \
   /opt/services/kanyo-harvard/clips/2026-01-22/
```

---

## Admin Dashboard (Optional)

The admin dashboard provides a web UI for managing streams.

Add to `docker-compose.yml`:

```yaml
services:
  # ... detection services ...

  dashboard:
    build: /opt/services/kanyo-code/admin/web
    container_name: kanyo-admin-web
    ports:
      - "5000:5000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${KANYO_CAM1_ROOT}:/data/harvard
      - ${KANYO_CAM2_ROOT}:/data/nsw
    restart: unless-stopped
```

Access at `http://your-server:5000`

---

## Operations

### Common Commands

```bash
# Start all
docker compose up -d

# Stop all
docker compose down

# Restart specific stream
docker compose restart harvard-gpu

# View logs
docker logs kanyo-harvard-gpu --tail 100 -f

# View all container status
docker compose ps

# Resource usage
docker stats
```

### Health Checks

```bash
# Check containers running
docker compose ps

# Check recent clips
ls -la /opt/services/kanyo-harvard/clips/$(date +%Y-%m-%d)/

# Check disk usage
du -sh /opt/services/kanyo-*/clips/

# Check ZFS (if using)
zfs list | grep kanyo
```

### Maintenance

```bash
# Delete clips older than 30 days
find /opt/services/kanyo-*/clips/ -name "*.mp4" -mtime +30 -delete
find /opt/services/kanyo-*/clips/ -name "*.jpg" -mtime +30 -delete

# Prune Docker resources
docker system prune -f
```

---

## Troubleshooting

### Container won't start

```bash
docker logs kanyo-detection --tail 100
```

Common issues:
- **"No such file or directory"** — Check volume paths exist
- **"Permission denied"** — Run `sudo chown -R 1000:1000 /opt/services/kanyo-*`
- **"NVIDIA driver"** — Install NVIDIA Container Toolkit

### YouTube stream fails

```bash
# Rebuild with latest yt-dlp
docker compose build --no-cache
docker compose up -d
```

### High memory usage

The `shm_size: '2gb'` setting is important for YOLO. If you see OOM errors:

```yaml
shm_size: '4gb'  # Increase shared memory
```

### GPU not detected

```bash
# Verify NVIDIA runtime
docker run --rm --gpus all nvidia/cuda:12.1-base nvidia-smi

# If that fails, install NVIDIA Container Toolkit:
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
```

---

## See Also

- [QUICKSTART.md](../QUICKSTART.md) — Quick setup guide
- [docs/adding-streams.md](../docs/adding-streams.md) — Detailed stream configuration
- [docs/sensing-logic.md](../docs/sensing-logic.md) — How detection works
