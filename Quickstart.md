# Kanyo Quick Start Guide

Get Kanyo running and detecting birds in under 15 minutes.

---

## Prerequisites

- **Docker** and **Docker Compose** installed
- **A YouTube live stream URL** (any bird/wildlife cam works)
- **Optional:** NVIDIA GPU with drivers for faster detection

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git
cd kanyo-contemplating-falcons-dev
```

---

## Step 2: Create Your Configuration

Copy the template and edit it:

```bash
cp configs/config.template.yaml config.yaml
```

Open `config.yaml` and set at minimum:

```yaml
# REQUIRED: Your YouTube stream URL
video_source: "https://www.youtube.com/watch?v=YOUR_VIDEO_ID"

# REQUIRED: Timezone for the stream location
timezone: "America/New_York"  # Use tz database names

# REQUIRED: A name for your stream
stream_name: "My Falcon Cam"
```

The template has sensible defaults for everything else. You can tune detection thresholds later.

---

## Step 3: Create Data Directories

```bash
mkdir -p clips logs
```

Kanyo will store video clips in `clips/` organized by date, and logs in `logs/`.

---

## Step 4: Choose Your Deployment

Kanyo supports three hardware configurations:

| Hardware | Compose File | Best For |
|----------|--------------|----------|
| NVIDIA GPU | `docker-compose.nvidia.yml` | Fastest detection, recommended |
| Intel iGPU | `docker-compose.vaapi.yml` | Good performance, integrated graphics |
| CPU only | `docker-compose.cpu.yml` | Works anywhere, slower |

---

## Step 5: Start Kanyo

For NVIDIA GPU:

```bash
docker compose -f docker/docker-compose.nvidia.yml up -d
```

For Intel iGPU:

```bash
docker compose -f docker/docker-compose.vaapi.yml up -d
```

For CPU only:

```bash
docker compose -f docker/docker-compose.cpu.yml up -d
```

---

## Step 6: Verify It's Working

Check the logs:

```bash
docker logs kanyo-detection --tail 50 -f
```

You should see:

```
INFO | Resolving stream URL: https://www.youtube.com/watch?v=...
INFO | ✅ Connected to stream
INFO | Frame 100: No detection
INFO | Frame 200: bird detected (confidence: 0.67)
```

Press `Ctrl+C` to stop following logs.

---

## Step 7: Set Up Telegram Notifications (Optional)

Telegram notifications let you know when birds arrive or depart.

### Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### Create a Channel

1. In Telegram, create a new **Channel** (not a group)
2. Make it **Public** and give it a username (e.g., `@my_falcon_alerts`)
3. Go to channel settings → Administrators → Add Administrator
4. Search for your bot by username and add it
5. Enable **Post Messages** permission for the bot

### Configure Kanyo

Create a `.env` file in your project directory:

```bash
echo "TELEGRAM_BOT_TOKEN=your_bot_token_here" > .env
```

Update your `config.yaml`:

```yaml
telegram_enabled: true
telegram_channel: "@my_falcon_alerts"
```

Restart Kanyo:

```bash
docker compose -f docker/docker-compose.nvidia.yml restart
```

---

## Step 8: Check Your Clips

After Kanyo has been running and detected some activity, check the clips directory:

```bash
ls -la clips/
```

You'll see date folders:

```
clips/
└── 2026-01-23/
    ├── events_2026-01-23.json
    ├── falcon_072315_arrival.mp4
    ├── falcon_072315_arrival.jpg
    ├── falcon_084530_departure.mp4
    └── falcon_072315_visit.mp4
```

---

## Configuration Reference

Key settings in `config.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `video_source` | — | YouTube stream URL (required) |
| `timezone` | UTC | Stream's local timezone |
| `detection_confidence` | 0.4 | Minimum confidence to detect (0.0–1.0) |
| `detection_confidence_ir` | 0.2 | Lower threshold for IR/night cameras |
| `frame_interval` | 3 | Process every Nth frame (higher = less CPU) |
| `exit_timeout` | 90 | Seconds without detection before "departed" |
| `roosting_threshold` | 1800 | Seconds (30 min) before "roosting" notification |
| `arrival_confirmation_seconds` | 10 | Seconds to confirm arrival isn't a false positive |
| `arrival_confirmation_ratio` | 0.3 | % of frames that must have detections to confirm |

See `configs/config.template.yaml` for the full list with documentation.

---

## Troubleshooting

### "YouTube precondition failed"

YouTube sometimes changes their API. Kanyo will automatically retry with a fallback client. If it persists:

```bash
docker compose -f docker/docker-compose.nvidia.yml build --no-cache
docker compose -f docker/docker-compose.nvidia.yml up -d
```

### No detections but bird is clearly visible

- Lower `detection_confidence` (try 0.3)
- For IR/night cameras, check `detection_confidence_ir`
- Verify `detect_any_animal: true` is set (YOLO sometimes classifies birds as other animals)

### Too many false positives

- Raise `detection_confidence` (try 0.5)
- Increase `arrival_confirmation_ratio` (try 0.4)
- Check if the camera has overlays or timestamps that trigger detections

### Telegram notifications not sending

1. Verify bot token in `.env`
2. Verify bot is admin of channel with post permission
3. Check logs for Telegram errors: `docker logs kanyo-detection | grep -i telegram`

---

## Next Steps

### Add the Viewer

The viewer provides a web interface for browsing clips and watching streams:

```bash
git clone https://github.com/sageframe-no-kaji/kanyo-viewer.git
cd kanyo-viewer
# See kanyo-viewer README for setup
```

### Add More Streams

See [docs/adding-streams.md](docs/adding-streams.md) for multi-stream deployment.

### Understand the Detection Logic

See [docs/sensing-logic.md](docs/sensing-logic.md) for how the state machine works.

---

## Getting Help

- **Issues:** [GitHub Issues](https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/issues)
- **Discussions:** [GitHub Discussions](https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/discussions)

---

## Quick Commands Reference

```bash
# Start
docker compose -f docker/docker-compose.nvidia.yml up -d

# Stop
docker compose -f docker/docker-compose.nvidia.yml down

# View logs
docker logs kanyo-detection --tail 100 -f

# Restart after config change
docker compose -f docker/docker-compose.nvidia.yml restart

# Rebuild (after code changes or to update yt-dlp)
docker compose -f docker/docker-compose.nvidia.yml build --no-cache
docker compose -f docker/docker-compose.nvidia.yml up -d

# Check disk usage
du -sh clips/
```
