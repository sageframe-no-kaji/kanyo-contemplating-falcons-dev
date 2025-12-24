# Kanyo Deployment Guide

## Directory Structure

Kanyo uses a distributed directory structure where each stream has its own isolated directory. This allows each stream to:
- Have its own ZFS dataset (for snapshots, quotas, compression)
- Be independently managed
- Scale to any number of streams

### Expected Structure

```
/opt/services/kanyo-admin/          # Admin/control directory
├── docker-compose.yml              # Service definitions
├── .env                            # Environment configuration
└── README.md                       # This deployment guide

/opt/services/kanyo-harvard/        # Stream 1 (each on separate ZFS dataset)
├── config.yaml                     # Stream-specific config
├── clips/                          # Generated video clips
│   └── YYYY-MM-DD/
│       ├── falcon_*.mp4
│       ├── falcon_*.jpg
│       └── events_*.json
└── logs/                           # Stream-specific logs
    └── kanyo.log

/opt/services/kanyo-nsw/            # Stream 2
├── config.yaml
├── clips/
└── logs/

/opt/services/kanyo-stream3/        # Additional streams...
├── config.yaml
├── clips/
└── logs/
```

## Environment Configuration

The `.env` file in `kanyo-admin` defines where each stream's data lives:

```bash
# Telegram credentials (shared by all streams)
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Stream directories (each can be on separate ZFS dataset)
KANYO_CAM1_ROOT=/opt/services/kanyo-harvard
KANYO_CAM2_ROOT=/opt/services/kanyo-nsw
# KANYO_CAM3_ROOT=/opt/services/kanyo-additional
```

## Initial Setup

### 1. Create Admin Directory

```bash
sudo mkdir -p /opt/services/kanyo-admin
cd /opt/services/kanyo-admin
```

### 2. Create Stream Directories

For each stream you want to monitor:

```bash
# Harvard stream
sudo mkdir -p /opt/services/kanyo-harvard/{clips,logs}

# NSW stream
sudo mkdir -p /opt/services/kanyo-nsw/{clips,logs}
```

**Optional:** Create ZFS datasets for each stream:
```bash
zfs create tank/kanyo-harvard
zfs create tank/kanyo-nsw

# Set quotas if desired
zfs set quota=100G tank/kanyo-harvard
zfs set compression=lz4 tank/kanyo-harvard
```

### 3. Create Stream Configs

Each stream needs a `config.yaml` in its directory:

```bash
# /opt/services/kanyo-harvard/config.yaml
video_source: "https://www.youtube.com/watch?v=..."
detection_confidence: 0.3
frame_interval: 1
telegram_enabled: true
telegram_channel: "@kanyo_harvard_falcon_cam"
# ... (see data/harvard/config.yaml for full example)
```

### 4. Set Up Admin Directory

```bash
cd /opt/services/kanyo-admin

# Copy docker-compose file
# (CPU version or GPU version depending on hardware)
cp /path/to/kanyo/docker-compose.yml .
# OR
cp /path/to/kanyo/docker-compose.nvidia.yml docker-compose.yml

# Create .env file
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_bot_token_here

KANYO_CAM1_ROOT=/opt/services/kanyo-harvard
KANYO_CAM2_ROOT=/opt/services/kanyo-nsw
EOF
```

### 5. Set Permissions

```bash
# Make sure directories are writable
sudo chmod -R 777 /opt/services/kanyo-*/clips
sudo chmod -R 777 /opt/services/kanyo-*/logs
```

### 6. Start Services

```bash
cd /opt/services/kanyo-admin
docker compose pull
docker compose up -d
```

## Adding New Streams

To add a new stream:

1. **Create stream directory:**
   ```bash
   sudo mkdir -p /opt/services/kanyo-newstream/{clips,logs}
   sudo chmod -R 777 /opt/services/kanyo-newstream/{clips,logs}
   ```

2. **Create config:**
   ```bash
   # Copy and modify existing config
   cp /opt/services/kanyo-harvard/config.yaml /opt/services/kanyo-newstream/config.yaml
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
       - ${KANYO_CAM3_ROOT}/config.yaml:/app/config.yaml:ro
       - ${KANYO_CAM3_ROOT}/clips:/app/clips
       - ${KANYO_CAM3_ROOT}/logs:/app/logs
   ```

5. **Restart:**
   ```bash
   cd /opt/services/kanyo-admin
   docker compose up -d
   ```

## Management Commands

All commands run from `/opt/services/kanyo-admin`:

```bash
# View logs
docker compose logs -f harvard-gpu
docker compose logs -f nsw-gpu

# Restart a stream
docker compose restart harvard-gpu

# Update to latest image
docker compose pull
docker compose up -d

# Check status
docker compose ps
```

## Monitoring

### Check Stream Logs
```bash
# View application logs
tail -f /opt/services/kanyo-harvard/logs/kanyo.log
tail -f /opt/services/kanyo-nsw/logs/kanyo.log
```

### Check Clips
```bash
# List today's clips
ls -lh /opt/services/kanyo-harvard/clips/$(date +%Y-%m-%d)/
ls -lh /opt/services/kanyo-nsw/clips/$(date +%Y-%m-%d)/
```

### Check Disk Usage
```bash
# Per stream
du -sh /opt/services/kanyo-*/clips
du -sh /opt/services/kanyo-*/logs

# If using ZFS
zfs list | grep kanyo
```

## Troubleshooting

### Stream not starting
```bash
# Check logs
docker compose logs harvard-gpu

# Check if config exists
ls -la /opt/services/kanyo-harvard/config.yaml

# Check permissions
ls -la /opt/services/kanyo-harvard/{clips,logs}
```

### No clips being created
```bash
# Check logs for tee/buffer errors
docker compose logs harvard-gpu | grep -i "tee\|buffer\|clip"

# Check directory permissions
ls -la /opt/services/kanyo-harvard/clips
```

### High disk usage
```bash
# Check clip sizes
du -sh /opt/services/kanyo-*/clips/*

# Old clips can be deleted manually or archived
# Set up ZFS snapshots for automatic archival
```

## Design Principles

1. **Each stream is independent** - Has its own directory with config, clips, logs
2. **Admin manages services** - docker-compose in admin dir, data in stream dirs
3. **Scalable** - Add streams by creating new directories and env vars
4. **ZFS-ready** - Each stream dir can be a separate dataset
5. **One image, many streams** - Same Docker image used by all streams
