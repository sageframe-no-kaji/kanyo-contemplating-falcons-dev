# Kanyo Docker Deployment Guide

**This is an opinionated Docker-based deployment guide.** It describes how to run Kanyo using Docker Compose with persistent storage for clips, logs, and configurations.

## Overview

Kanyo runs as Docker containers (one per camera stream). Each stream needs three persistent directories:
- **`clips/`** - Generated video clips and thumbnails (grows over time)
- **`logs/`** - Application logs
- **`config.yaml`** - Stream-specific configuration file

This guide provides:
1. **Base Docker deployment** - Simple setup with local directories
2. **Advanced ZFS deployment** - Multi-stream setup with ZFS datasets for snapshots and quotas

## Persistent Directories

Each camera stream requires these directories to be mounted into the container:

| Mount Point | Purpose | Size Growth |
|-------------|---------|-------------|
| `/app/config.yaml` | Stream config (read-only) | ~2KB (static) |
| `/app/clips` | Video clips and events | ~1-5GB/day (depends on activity) |
| `/app/logs` | Application logs | ~10-50MB/day |
| `/app/src` | Source code (optional) | ~1MB (static) |

---

## Base Deployment (Simple Docker)

This is the simplest way to deploy Kanyo with Docker Compose.

### Directory Structure

Kanyo uses a distributed directory structure where each stream has its own isolated directory. This allows each stream to:
- Have independent storage
- Be independently managed
- Scale to any number of streams

### Expected Structure

```
/opt/services/kanyo-admin/          # Docker Compose control directory
├── docker-compose.yml              # Service definitions
└── .env                            # Environment configuration

/opt/services/kanyo-harvard/        # Stream 1 persistent data
├── config.yaml                     # Stream-specific config
├── clips/                          # Generated video clips
│   └── YYYY-MM-DD/
│       ├── falcon_*.mp4
│       ├── falcon_*.jpg
│       └── events_*.json
└── logs/                           # Stream-specific logs
    └── kanyo.log

/opt/services/kanyo-nsw/            # Stream 2 persistent data
├── config.yaml
├── clips/
└── logs/

/opt/services/kanyo-code/           # Optional: Source code (for development)
└── src/
```

### Quick Start

```bash
# 1. Create directories
sudo mkdir -p /opt/services/kanyo-admin
sudo mkdir -p /opt/services/kanyo-harvard/{clips,logs}
sudo mkdir -p /opt/services/kanyo-nsw/{clips,logs}

# 2. Set permissions (containers run as UID 1000)
sudo chown -R 1000:1000 /opt/services/kanyo-harvard
sudo chown -R 1000:1000 /opt/services/kanyo-nsw

# 3. Create stream configs
# Copy from configs/config.template.yaml and customize for each stream
sudo cp configs/config.template.yaml /opt/services/kanyo-harvard/config.yaml
sudo cp configs/config.template.yaml /opt/services/kanyo-nsw/config.yaml
# Edit each config with stream-specific settings (video_source, telegram_channel, etc.)

# 4. Set up admin directory
cd /opt/services/kanyo-admin

# Copy docker-compose file (choose CPU, VAAPI, or NVIDIA variant)
cp docker/docker-compose.nvidia.yml docker-compose.yml

# Create .env file
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_bot_token_here
KANYO_CAM1_ROOT=/opt/services/kanyo-harvard
KANYO_CAM2_ROOT=/opt/services/kanyo-nsw
KANYO_CODE_ROOT=/opt/services/kanyo-code
EOF

# 5. Clone code repository (for source mounting)
sudo git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git /opt/services/kanyo-code
sudo chown -R 1000:1000 /opt/services/kanyo-code

# 6. Start services
docker compose pull
docker compose up -d

# 7. Check logs
docker compose logs -f
```

---

## Advanced Deployment (ZFS + Multiple Streams)

**This is an opinionated setup using ZFS for dataset isolation, snapshots, quotas, and compression.**

### Why ZFS?

- **Snapshots** - Point-in-time backups of clips before cleanup
- **Quotas** - Prevent runaway disk usage per stream
- **Compression** - LZ4 compression reduces storage (20-30% savings typical)
- **Independent datasets** - Each stream isolated, easy to move/backup

### ZFS Setup
### ZFS Setup

```bash
# Create ZFS datasets for each stream
zfs create tank/services/kanyo-admin
zfs create tank/services/kanyo-harvard
zfs create tank/services/kanyo-nsw
zfs create tank/services/kanyo-code

# Set compression (LZ4 is fast, 20-30% savings)
zfs set compression=lz4 tank/services/kanyo-harvard
zfs set compression=lz4 tank/services/kanyo-nsw

# Set quotas to prevent runaway disk usage
zfs set quota=200G tank/services/kanyo-harvard
zfs set quota=200G tank/services/kanyo-nsw

# Enable automatic snapshots (requires zfs-auto-snapshot package)
zfs set com.sun:auto-snapshot=true tank/services/kanyo-harvard
zfs set com.sun:auto-snapshot=true tank/services/kanyo-nsw

# Create mount points
sudo mkdir -p /opt/services
zfs set mountpoint=/opt/services/kanyo-admin tank/services/kanyo-admin
zfs set mountpoint=/opt/services/kanyo-harvard tank/services/kanyo-harvard
zfs set mountpoint=/opt/services/kanyo-nsw tank/services/kanyo-nsw
zfs set mountpoint=/opt/services/kanyo-code tank/services/kanyo-code
```

### Complete ZFS Deployment

```bash
# 1. Create directory structure within ZFS datasets
sudo mkdir -p /opt/services/kanyo-harvard/{clips,logs}
sudo mkdir -p /opt/services/kanyo-nsw/{clips,logs}

# 2. Set ownership
sudo chown -R 1000:1000 /opt/services/kanyo-harvard
sudo chown -R 1000:1000 /opt/services/kanyo-nsw
sudo chown -R 1000:1000 /opt/services/kanyo-code

# 3. Clone code repository
cd /opt/services/kanyo-code
sudo -u $(id -un 1000) git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git .

# 4. Create stream configs
sudo cp /opt/services/kanyo-code/configs/config.template.yaml /opt/services/kanyo-harvard/config.yaml
sudo cp /opt/services/kanyo-code/configs/config.template.yaml /opt/services/kanyo-nsw/config.yaml
# Edit each config file with stream-specific settings

# 5. Set up docker-compose in admin directory
cd /opt/services/kanyo-admin
sudo cp /opt/services/kanyo-code/docker/docker-compose.nvidia.yml docker-compose.yml

# 6. Create .env
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_bot_token_here
KANYO_CAM1_ROOT=/opt/services/kanyo-harvard
KANYO_CAM2_ROOT=/opt/services/kanyo-nsw
KANYO_CODE_ROOT=/opt/services/kanyo-code
EOF

# 7. Start services
docker compose pull
docker compose up -d
```

### ZFS Monitoring

```bash
# Check dataset usage
zfs list | grep kanyo

# Check compression ratio
zfs get compressratio tank/services/kanyo-harvard

# List snapshots
zfs list -t snapshot | grep kanyo

# Manually create snapshot before cleanup
zfs snapshot tank/services/kanyo-harvard@$(date +%Y%m%d)
```

---

## Configuration

### Environment Variables (.env)

The `.env` file in `kanyo-admin` defines where each stream's data lives:

```bash
# Telegram credentials (shared by all streams)
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Stream directories
KANYO_CAM1_ROOT=/opt/services/kanyo-harvard
KANYO_CAM2_ROOT=/opt/services/kanyo-nsw

# Source code directory (optional, for development)
KANYO_CODE_ROOT=/opt/services/kanyo-code
```

### Stream Configuration (config.yaml)

Each stream needs its own `config.yaml`. See `configs/config.template.yaml` for full documentation.

Example minimal config:

```yaml
video_source: "https://www.youtube.com/watch?v=XYZ..."
detection_confidence: 0.35
frame_interval: 2
telegram_enabled: true
telegram_channel: "@your_channel"
```

---

## Adding New Streams

To add a new stream to an existing deployment:

To add a new stream to an existing deployment:

### Base Docker

1. **Create stream directory:**
   ```bash
   sudo mkdir -p /opt/services/kanyo-newstream/{clips,logs}
   sudo chown -R 1000:1000 /opt/services/kanyo-newstream
   ```

2. **Create config:**
   ```bash
   sudo cp configs/config.template.yaml /opt/services/kanyo-newstream/config.yaml
   # Edit with stream-specific settings
   ```

3. **Add to .env:**
   ```bash
   # Add to /opt/services/kanyo-admin/.env
   KANYO_CAM3_ROOT=/opt/services/kanyo-newstream
   ```

4. **Add service to docker-compose.yml:**
   ```yaml
   newstream-gpu:
     <<: *kanyo-gpu-service
     container_name: kanyo-newstream-gpu
     volumes:
       - ${KANYO_CODE_ROOT:-/opt/services/kanyo-code}/src:/app/src:ro
       - ${KANYO_CAM3_ROOT}/config.yaml:/app/config.yaml:ro
       - ${KANYO_CAM3_ROOT}/clips:/app/clips
       - ${KANYO_CAM3_ROOT}/logs:/app/logs
   ```

5. **Restart:**
   ```bash
   cd /opt/services/kanyo-admin
   docker compose up -d
   ```

### ZFS Deployment

1. **Create ZFS dataset:**
   ```bash
   zfs create tank/services/kanyo-newstream
   zfs set compression=lz4 tank/services/kanyo-newstream
   zfs set quota=200G tank/services/kanyo-newstream
   zfs set mountpoint=/opt/services/kanyo-newstream tank/services/kanyo-newstream
   ```

2. **Follow base deployment steps 1-5 above**

---

## Management Commands

All commands run from `/opt/services/kanyo-admin`:

```bash
# View logs for specific stream
docker compose logs -f harvard-gpu
docker compose logs -f nsw-gpu

# View all logs
docker compose logs -f

# Restart a specific stream
docker compose restart harvard-gpu

# Restart all streams
docker compose restart

# Update to latest code and rebuild
cd /opt/services/kanyo-code && git pull
cd /opt/services/kanyo-admin
docker compose down
docker compose up -d --build

# Update to latest pre-built image (no rebuild)
docker compose pull
docker compose up -d

# Check status
docker compose ps

# Stop all streams
docker compose down

# Remove old images
docker image prune -f
```

## Monitoring

### Check Stream Logs
```bash
# View application logs
tail -f /opt/services/kanyo-harvard/logs/kanyo.log
tail -f /opt/services/kanyo-nsw/logs/kanyo.log

# Or via Docker
docker compose logs -f harvard-gpu
```

### Check Clips
```bash
# List today's clips
ls -lh /opt/services/kanyo-harvard/clips/$(date +%Y-%m-%d)/
ls -lh /opt/services/kanyo-nsw/clips/$(date +%Y-%m-%d)/
```

### Check Disk Usage
```bash
# Per stream (base deployment)
du -sh /opt/services/kanyo-*/clips
du -sh /opt/services/kanyo-*/logs

# With ZFS
zfs list | grep kanyo

# Show compression savings
zfs get used,compressratio,referenced tank/services/kanyo-harvard
```

---

---

## Troubleshooting

### Stream not starting
```bash
# Check logs
docker compose logs harvard-gpu

# Check if config exists and is readable
ls -la /opt/services/kanyo-harvard/config.yaml

# Check permissions (should be owned by UID 1000)
ls -la /opt/services/kanyo-harvard/{clips,logs}

# Validate config syntax
docker run --rm -v /opt/services/kanyo-harvard/config.yaml:/config.yaml \
  ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:nvidia \
  python -c "import yaml; yaml.safe_load(open('/config.yaml'))"
```

### No clips being created
```bash
# Check logs for errors
docker compose logs harvard-gpu | grep -i "error\|exception\|failed"

# Check buffer/tee logs
docker compose logs harvard-gpu | grep -i "tee\|buffer\|clip"

# Check directory permissions (must be writable by UID 1000)
ls -la /opt/services/kanyo-harvard/clips

# Test clip creation manually
docker exec -it kanyo-harvard-gpu ls -la /app/clips
```

### High disk usage
```bash
# Check clip sizes by day
du -sh /opt/services/kanyo-*/clips/*

# ZFS: Check compression ratio
zfs get compressratio tank/services/kanyo-harvard

# Clean old clips (manual, or set up cron job)
find /opt/services/kanyo-harvard/clips -type f -mtime +30 -delete

# ZFS: Create snapshot before cleanup
zfs snapshot tank/services/kanyo-harvard@before-cleanup-$(date +%Y%m%d)
```

### Permission errors
```bash
# All persistent directories must be owned by UID 1000 (container user)
sudo chown -R 1000:1000 /opt/services/kanyo-harvard
sudo chown -R 1000:1000 /opt/services/kanyo-nsw

# Config file must be readable
sudo chmod 644 /opt/services/kanyo-*/config.yaml
```

### GPU not detected (NVIDIA)
```bash
# Verify nvidia-docker runtime is installed
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi

# Check docker compose GPU config
docker compose config | grep -A 10 devices
```

---

## Design Principles

1. **Docker-first** - All deployment via Docker Compose, no manual Python installs
2. **Each stream is independent** - Has its own directory with config, clips, logs
3. **Persistent storage** - Clips, logs, configs survive container restarts/rebuilds
4. **Admin manages services** - docker-compose in admin dir, data in stream dirs
5. **Scalable** - Add streams by creating new directories and env vars
6. **ZFS-ready** - Each stream dir can be a separate dataset with snapshots/quotas
7. **One image, many streams** - Same Docker image used by all streams
8. **Source mounting (optional)** - Mount `/app/src` from host for development without rebuilds
