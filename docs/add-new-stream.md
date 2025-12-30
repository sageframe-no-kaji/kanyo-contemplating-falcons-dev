# Adding a New Falcon Cam to Kanyo

## Prerequisites

- SSH access to shingan
- YouTube live stream URL for the new camera
- Know the camera's timezone

## Steps

### 1. Create Directory Structure

```bash
# Replace {name} with lowercase identifier (e.g., melbourne, cornell)
sudo mkdir -p /opt/services/kanyo-{name}/{clips,logs}
sudo chown -R atmarcus:atmarcus /opt/services/kanyo-{name}
```

### 2. Create Config File

```bash
nano /opt/services/kanyo-{name}/config.yaml
```

Paste and edit:

```yaml
stream_name: "Your Camera Name"
video_source: "https://www.youtube.com/watch?v=XXXXXXXXX"

detection_confidence: 0.5
frame_interval: 3
detect_any_animal: false

exit_timeout: 90
roosting_threshold: 1800

timezone: "+00:00"  # Camera's timezone offset

telegram_enabled: true
telegram_channel: "@your_channel"
telegram_bot_token_env: "TELEGRAM_BOT_TOKEN"

buffer_duration: 120
clip_before_event: 5
clip_after_event: 3

arrival_cooldown: 300
departure_cooldown: 300
```

### 3. Add Detection Container

```bash
nano /opt/services/kanyo-nvidia/docker-compose.yml
```

Add new service (copy existing block, modify):

```yaml
  kanyo-{name}-gpu:
    image: kanyo-detection:latest
    container_name: kanyo-{name}-gpu
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    volumes:
      - /opt/services/kanyo-{name}:/data
    env_file:
      - .env
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

### 4. Add Admin Volume Mount

```bash
nano /opt/services/kanyo-admin/docker-compose.yml
```

Add to the `dashboard` service volumes:

```yaml
      - /opt/services/kanyo-{name}:/data/{name}:ro
```

### 5. Start Everything

```bash
# Start new detection container
cd /opt/services/kanyo-nvidia
docker compose up -d kanyo-{name}-gpu

# Restart admin to see new stream
cd /opt/services/kanyo-admin
docker compose restart dashboard
```

### 6. Verify

```bash
# Check container is running
docker ps | grep kanyo-{name}

# Check logs
docker logs kanyo-{name}-gpu --tail 50

# Visit admin GUI
open http://shingan.lan:5000
```

## Common Timezones

|Location|Offset|
|---|---|
|US Pacific|-08:00|
|US Eastern|-05:00|
|UK|+00:00|
|Central Europe|+01:00|
|Australia East|+10:00|
|Australia (NSW DST)|+11:00|
|New Zealand|+12:00|

## Troubleshooting

**Container won't start:**

```bash
docker logs kanyo-{name}-gpu
```

**Stream not appearing in admin:**

- Check volume mount is correct in admin docker-compose.yml
- Restart admin: `docker compose restart dashboard`

**No detections:**

- Check YouTube URL is a live stream (not a video)
- Try lowering `detection_confidence` to 0.3
- Check logs for YOLO loading errors

## Removing a Camera

```bash
# Stop container
docker stop kanyo-{name}-gpu
docker rm kanyo-{name}-gpu

# Remove from docker-compose.yml (edit both files)
nano /opt/services/kanyo-nvidia/docker-compose.yml
nano /opt/services/kanyo-admin/docker-compose.yml

# Optionally delete data (CAUTION: deletes all clips!)
# rm -rf /opt/services/kanyo-{name}

# Restart admin
cd /opt/services/kanyo-admin
docker compose restart dashboard
```