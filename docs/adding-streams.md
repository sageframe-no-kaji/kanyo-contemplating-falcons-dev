# Adding Streams to Kanyō

This guide covers deploying new camera streams to the Kanyō monitoring system.

---

## Overview

Adding a stream involves three layers:

| Layer | What | Files to Edit |
|-------|------|---------------|
| **Detection** | Monitors the stream, detects birds, records clips | admin `docker-compose.yml`, `config.yaml`, `.env` |
| **Admin** | Shows stream in admin dashboard | admin `docker-compose.yml` (dashboard volumes) |
| **Viewer** | Shows stream on public site | restart only — auto-discovers from data directory |

The viewer is **registry-free**: it scans `/opt/services/` for any directory containing a `config.yaml` and registers it automatically. No viewer config changes are required when adding or removing streams.

You can add streams to just detection (headless monitoring), or all three layers for full visibility.

---

## Prerequisites

- Docker and Docker Compose installed
- Access to the server running Kanyō (via SSH with sudo)
- A YouTube live stream URL
- ZFS pool configured at `rpool/sage/kanyo/` (required on production kanyo host)
- (Optional) Telegram bot token for notifications
- (Optional) NVIDIA GPU for faster detection

> **Note:** The detection container image must include `tzdata`. Without it, Python's `zoneinfo` silently falls back to UTC, causing all clip timestamps and directory names to be UTC-based instead of stream-local time. This is a hard requirement — `tzdata>=2024.1` is in `requirements.txt`; do not remove it.

---

## Part 1: Detection System

This gets the stream monitored with clips being recorded.

### Step 1: Create ZFS Dataset and Stream Directory

On the production kanyo host, create a ZFS dataset first so clips get compression and quota protection:

```bash
sudo zfs create rpool/sage/kanyo/STREAMID
mkdir -p /opt/services/kanyo-STREAMID/{clips,logs}
chown -R atmarcus:atmarcus /opt/services/kanyo-STREAMID
```

Replace `STREAMID` with a short identifier (lowercase, hyphens ok): `harvard`, `nsw`, `fortwayne`, `umass`, etc.

The full directory name must be `kanyo-STREAMID` — the viewer uses the directory name as the stream's `id` in the API.

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
   - Name: "Kanyō STREAMID Alerts" (or whatever you want)
   - Type: **Public**
   - Username: `kanyo_STREAMID`

2. **Add Your Bot as Admin**
   - Go to channel settings → Administrators → Add Administrator
   - Search for your Kanyō bot (e.g., "Kanyō Falcon Alert Bot")
   - Enable "Post Messages" permission
   - Save

3. **Update config.yaml**
   ```yaml
   telegram_enabled: true
   telegram_channel: "@kanyo_STREAMID"
   ```

### Step 4: Add to Detection Docker Compose

In `/opt/services/kanyo-admin/.env`, add the stream root path (use the next available CAM number):

```bash
KANYO_CAMn_ROOT=/opt/services/kanyo-STREAMID
```

In `/opt/services/kanyo-admin/docker-compose.yml`, add a new service block:

```yaml
services:
  # ... existing services ...

  STREAMID-gpu:
    <<: *kanyo-gpu-service
    container_name: kanyo-STREAMID-gpu
    command: ["/bin/sh", "-c", "umask 027 && exec python -m kanyo.detection.buffer_monitor"]
    volumes:
      - ${KANYO_CODE_ROOT:-/opt/services/kanyo-code}/src:/app/src:ro
      - ${KANYO_CODE_ROOT:-/opt/services/kanyo-code}/cookies.txt:/app/cookies.txt:rw
      - ${KANYO_CAMn_ROOT:-./data/STREAMID}/config.yaml:/app/config.yaml:ro
      - ${KANYO_CAMn_ROOT:-./data/STREAMID}/clips:/app/clips
      - ${KANYO_CAMn_ROOT:-./data/STREAMID}/logs:/app/logs
```

Also add the stream volume to the `dashboard` service in the same file:

```yaml
  dashboard:
    volumes:
      # ... existing volumes ...
      - ${KANYO_CAMn_ROOT:-./data/STREAMID}:/data/kanyo-STREAMID
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

The dashboard volume is added in Step 4 of Part 1 above (both detection and dashboard are in the same `kanyo-admin/docker-compose.yml`). After editing:

```bash
cd /opt/services/kanyo-admin
docker compose up -d dashboard
```

The new stream should appear in the admin interface.

---

## Part 3: Public Viewer

The viewer uses **auto-discovery** — no config file edits required. The viewer container mounts `/opt/services` as `/data` and scans for any subdirectory containing a `config.yaml`. Creating the stream directory with its config is sufficient.

### Step 1: Restart the Viewer

```bash
cd /opt/services/kanyo-viewer
docker compose restart
```

The stream will appear automatically. The viewer caches the stream list on startup, so a restart is required whenever streams are added or removed.

### Step 2: Verify

```bash
curl -s http://localhost:3000/api/streams | python3 -m json.tool | grep '"id"'
```

You should see `kanyo-STREAMID` in the list. Visit the public viewer URL to confirm it appears on the landing page.

> **For complete viewer documentation and features, see the [kanyo-viewer README](https://github.com/sageframe-no-kaji/kanyo-viewer).**

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

1. Confirm `/opt/services/kanyo-STREAMID/config.yaml` exists and is valid YAML
2. Restart the viewer: `cd /opt/services/kanyo-viewer && docker compose restart`
3. Check the API directly: `curl http://localhost:3000/api/streams`
4. Check viewer logs: `docker logs kanyo-viewer --tail 50`

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

## Removing a Stream

To fully remove a stream from the system:

1. **Stop the detection container:**
   ```bash
   cd /opt/services/kanyo-admin
   docker compose stop STREAMID-gpu
   docker compose rm -f STREAMID-gpu
   ```

2. **Edit admin `docker-compose.yml`:** Remove the service block and its dashboard volume line.

3. **Edit admin `.env`:** Remove the `KANYO_CAMn_ROOT` line.

4. **Restart admin dashboard:**
   ```bash
   docker compose up -d dashboard
   ```

5. **Restart the viewer:**
   ```bash
   cd /opt/services/kanyo-viewer && docker compose restart
   ```

6. **Delete data and destroy ZFS dataset (irreversible):**
   ```bash
   sudo rm -rf /opt/services/kanyo-STREAMID
   sudo zfs destroy rpool/sage/kanyo/STREAMID
   ```

---

## See Also

- [Quickstart.md](../Quickstart.md) — Get your first stream running
- [sensing-logic.md](sensing-logic.md) — How the detection state machine works
- [kanyo-viewer](https://github.com/sageframe-no-kaji/kanyo-viewer) — Viewer architecture and API
