# Adding Streams to Kanyo

This guide covers deploying new camera streams to the Kanyo monitoring system.

---

## Overview

Adding a stream involves three layers:

| Layer | What | Files to Edit |
|-------|------|---------------|
| **Detection** | Monitors the stream, detects birds, records clips | `docker-compose.yml`, `config.yaml` |
| **Admin** | Shows stream in admin dashboard | `docker-compose.yml` (dashboard volumes) |
| **Viewer** | Shows stream on public site | `docker-compose.yml`, `streams.yaml` |

You can add streams to just detection (headless monitoring), or all three layers for full visibility.

---

## Prerequisites

- Docker and Docker Compose installed
- Access to the server running Kanyo
- A YouTube live stream URL
- (Optional) Telegram bot token for notifications
- (Optional) NVIDIA GPU for faster detection

---

## Part 1: Detection System

This gets the stream monitored with clips being recorded.

### Step 1: Create Stream Directory

```bash
sudo mkdir -p /opt/services/kanyo-STREAMID/{clips,logs,data}
sudo chown -R 1000:1000 /opt/services/kanyo-STREAMID
```

Replace `STREAMID` with a short identifier (lowercase, no spaces): `harvard`, `nsw`, `humspot`, etc.

### Step 2: Create Configuration

Create `/opt/services/kanyo-STREAMID/config.yaml`:

```yaml
# === REQUIRED ===
stream_name: "My Falcon Cam"
video_source: "https://www.youtube.com/watch?v=VIDEO_ID"
timezone: "America/New_York"

# === DETECTION ===
detection_confidence: 0.4          # Daytime threshold (0.0-1.0)
detection_confidence_ir: 0.2       # Night/IR camera threshold
frame_interval: 3                  # Process every Nth frame
detect_any_animal: true            # Accept any animal class as "bird"
model_path: models/yolov8n.pt

# === STATE MACHINE ===
exit_timeout: 90                   # Seconds without detection = departed
roosting_threshold: 1800           # Seconds (30 min) before roosting alert
arrival_confirmation_seconds: 10   # Time window to confirm arrival
arrival_confirmation_ratio: 0.3    # % of frames needed to confirm

# === CLIPS ===
clips_dir: clips
clip_arrival_before: 15            # Seconds before arrival in clip
clip_arrival_after: 30             # Seconds after arrival in clip
clip_departure_before: 30          # Seconds before departure in clip
clip_departure_after: 15           # Seconds after departure in clip
clip_crf: 23                       # Video quality (lower = better, bigger)
clip_fps: 30
buffer_seconds: 60                 # Frame buffer for pre-event capture

# === NOTIFICATIONS ===
telegram_enabled: true
telegram_channel: "@kanyo_STREAMID"
notification_cooldown_minutes: 5

# === LOGGING ===
log_level: INFO
log_file: logs/kanyo.log

# === DISPLAY METADATA (for viewer) ===
display:
  short_name: "Stream Name"
  location: "City, State/Country"
  species: "Falco peregrinus"
  coordinates:
    - 42.3736      # latitude
    - -71.1097     # longitude
  maintainer: "Organization Name"
  maintainer_url: "https://example.com"
  description: "Description of the camera location and birds."
```

**Key settings to customize:**
- `stream_name` — Display name
- `video_source` — YouTube URL
- `timezone` — Use [tz database names](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)
- `telegram_channel` — Your Telegram channel username
- `display` section — Metadata shown in the viewer

### Step 3: Set Up Telegram (Optional)

If you want notifications when birds arrive/depart:

1. **Create a Channel**
   - Open Telegram → Menu → New Channel
   - Name: "Kanyo STREAMID Alerts" (or whatever you want)
   - Type: **Public**
   - Username: `kanyo_STREAMID`

2. **Add Your Bot as Admin**
   - Go to channel settings → Administrators → Add Administrator
   - Search for your Kanyo bot (e.g., "Kanyo Falcon Alert Bot")
   - Enable "Post Messages" permission
   - Save

3. **Update config.yaml**
   ```yaml
   telegram_enabled: true
   telegram_channel: "@kanyo_STREAMID"
   ```

### Step 4: Add to Docker Compose

Edit your detection `docker-compose.yml` and add a new service:

```yaml
services:
  # ... existing services ...

  STREAMID-gpu:
    <<: *kanyo-gpu-service
    container_name: kanyo-STREAMID-gpu
    command: ["/bin/sh", "-c", "umask 027 && exec python -m kanyo.detection.buffer_monitor"]
    volumes:
      - ${KANYO_CODE_ROOT:-/opt/services/kanyo-code}/src:/app/src:ro
      - /opt/services/kanyo-STREAMID/config.yaml:/app/config.yaml:ro
      - /opt/services/kanyo-STREAMID/clips:/app/clips
      - /opt/services/kanyo-STREAMID/logs:/app/logs
```

If you're using environment variables for paths, also add to `.env`:

```bash
KANYO_STREAMID_ROOT=/opt/services/kanyo-STREAMID
```

### Step 5: Start Detection

```bash
docker compose up -d STREAMID-gpu
```

Verify it's running:

```bash
docker logs kanyo-STREAMID-gpu --tail 50 -f
```

You should see:
```
INFO | Resolving stream URL: https://www.youtube.com/watch?v=...
INFO | ✅ Connected to stream
INFO | Frame 100: No detection
INFO | Frame 200: bird detected (confidence: 0.67)
```

---

## Part 2: Admin Dashboard

This makes the stream visible in the admin interface.

### Step 1: Add Volume Mount

Edit your admin `docker-compose.yml` and add the stream to the dashboard service:

```yaml
services:
  dashboard:
    # ... existing config ...
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${KANYO_CAM1_ROOT:-./data/cam1}:/data/cam1
      - ${KANYO_CAM2_ROOT:-./data/cam2}:/data/cam2
      - /opt/services/kanyo-STREAMID:/data/STREAMID    # ADD THIS
```

### Step 2: Restart Dashboard

```bash
docker compose up -d dashboard
```

The new stream should appear in the admin interface.

---

## Part 3: Public Viewer

This makes the stream visible on the public website.
**For complete viewer documentation and features, see the [kanyo-viewer README](https://github.com/sageframe-no-kaji/kanyo-viewer).**


### Step 1: Add Volume Mounts

Edit `/opt/services/kanyo-viewer/docker-compose.yml`:

```yaml
services:
  viewer:
    # ... existing config ...
    volumes:
      # ... existing volumes ...
      - /opt/services/kanyo-STREAMID:/data/STREAMID:ro
      - /opt/services/kanyo-STREAMID/config.yaml:/configs/STREAMID/config.yaml:ro
```

### Step 2: Register the Stream

Edit `/opt/services/kanyo-viewer/backend/streams.yaml`:

```yaml
streams:
  # ... existing streams ...

  kanyo-STREAMID:
    config_path: "/configs/STREAMID/config.yaml"
    data_path: "/data/STREAMID"
```

### Step 3: Rebuild and Restart

```bash
cd /opt/services/kanyo-viewer
docker compose build viewer
docker compose up -d viewer
```

### Step 4: Verify

Visit your viewer URL. The new stream should appear on the landing page.

---

## Configuration Reference

### Detection Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `detection_confidence` | 0.4 | Minimum YOLO confidence for daytime |
| `detection_confidence_ir` | 0.2 | Minimum confidence for IR/night cameras |
| `frame_interval` | 3 | Process every Nth frame (higher = less CPU) |
| `detect_any_animal` | true | Accept any animal class, not just "bird" |

### State Machine Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `exit_timeout` | 90 | Seconds without detection before "departed" |
| `roosting_threshold` | 1800 | Seconds (30 min) before "roosting" notification |
| `arrival_confirmation_seconds` | 10 | Window to confirm arrival isn't false positive |
| `arrival_confirmation_ratio` | 0.3 | Fraction of frames that must have detections |

### Clip Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `clip_arrival_before` | 15 | Seconds before arrival to include |
| `clip_arrival_after` | 30 | Seconds after arrival to include |
| `clip_departure_before` | 30 | Seconds before departure to include |
| `clip_departure_after` | 15 | Seconds after departure to include |
| `clip_crf` | 23 | Video quality (18=high, 23=medium, 28=low) |
| `buffer_seconds` | 60 | How much pre-event footage to keep in memory |

### Notification Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `telegram_enabled` | false | Send Telegram notifications |
| `telegram_channel` | — | Channel username (e.g., `@my_channel`) |
| `notification_cooldown_minutes` | 5 | Minimum time between notifications |

---

## Troubleshooting

### Detection not starting

```bash
docker logs kanyo-STREAMID-gpu --tail 100
```

Common issues:
- **"No such file or directory"** — Check volume mounts and paths
- **"Permission denied"** — Run `sudo chown -R 1000:1000 /opt/services/kanyo-STREAMID`
- **"YouTube precondition failed"** — YouTube API changed; rebuild container with `docker compose build --no-cache`

### No detections but bird is visible

- Lower `detection_confidence` (try 0.3)
- For night cameras, check `detection_confidence_ir`
- Ensure `detect_any_animal: true` (YOLO sometimes misclassifies birds)

### Too many false arrivals

- Raise `detection_confidence` (try 0.5)
- Increase `arrival_confirmation_ratio` (try 0.4)
- Increase `arrival_confirmation_seconds` (try 15)

### Telegram not sending

1. Verify bot token in `.env`
2. Verify bot is admin of channel
3. Check logs: `docker logs kanyo-STREAMID-gpu | grep -i telegram`

### Stream not appearing in viewer

1. Check volume mounts in viewer's `docker-compose.yml`
2. Check `streams.yaml` has the correct entry
3. Rebuild viewer: `docker compose build viewer && docker compose up -d viewer`

---

## Quick Reference

### Start/Stop Commands

```bash
# Start a stream
docker compose up -d STREAMID-gpu

# Stop a stream
docker stop kanyo-STREAMID-gpu

# Restart a stream
docker restart kanyo-STREAMID-gpu

# View logs
docker logs kanyo-STREAMID-gpu --tail 100 -f
```

### Check Status

```bash
# All running containers
docker ps | grep kanyo

# Disk usage per stream
du -sh /opt/services/kanyo-*/clips/

# Recent clips
ls -la /opt/services/kanyo-STREAMID/clips/$(date +%Y-%m-%d)/
```

### Maintenance

```bash
# Delete clips older than 30 days
find /opt/services/kanyo-STREAMID/clips/ -name "*.mp4" -mtime +30 -delete
find /opt/services/kanyo-STREAMID/clips/ -name "*.jpg" -mtime +30 -delete
```

---

## ZFS Setup (Optional)

If using ZFS for storage, create a dataset before the stream directory:

```bash
sudo zfs create rpool/sage/kanyo/STREAMID
sudo zfs set quota=500G rpool/sage/kanyo/STREAMID
sudo mkdir -p /opt/services/kanyo-STREAMID/{clips,logs,data}
sudo chown -R 1000:1000 /opt/services/kanyo-STREAMID
```

Benefits:
- Compression (saves ~20% disk space on video)
- Snapshots for backup
- Quotas to prevent runaway storage

---

## See Also

- [QUICKSTART.md](../QUICKSTART.md) — Get your first stream running
- [sensing-logic.md](sensing-logic.md) — How the detection state machine works
