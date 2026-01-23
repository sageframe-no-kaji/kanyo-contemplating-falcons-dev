# Kanyo Quick Start Guide

Get Kanyo running and detecting birds in 15 minutes.

---

## Prerequisites

- **A YouTube live stream URL** (any bird/wildlife cam works)
- **One of:**
  - Docker and Docker Compose (recommended)
  - Python 3.11+ (for bare metal)
- **Optional:** NVIDIA GPU for faster detection

---

## Option A: Docker (Recommended)

### Step 1: Clone the Repository

```bash
git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git
cd kanyo-contemplating-falcons-dev
```

### Step 2: Create Your Configuration

```bash
cp configs/config.template.yaml config.yaml
```

Edit `config.yaml` with at minimum:

```yaml
video_source: "https://www.youtube.com/watch?v=YOUR_VIDEO_ID"
timezone: "America/New_York"  # Stream's local timezone
stream_name: "My Falcon Cam"
```

### Step 3: Create Data Directories

```bash
mkdir -p clips logs
```

### Step 4: Choose Your Hardware

| Hardware | Compose File | Best For |
|----------|--------------|----------|
| NVIDIA GPU | `docker/docker-compose.nvidia.yml` | Fastest detection |
| Intel iGPU | `docker/docker-compose.vaapi.yml` | Good performance |
| CPU only | `docker/docker-compose.cpu.yml` | Works anywhere |

### Step 5: Start Kanyo

```bash
# For NVIDIA GPU:
docker compose -f docker/docker-compose.nvidia.yml up -d

# For Intel iGPU:
docker compose -f docker/docker-compose.vaapi.yml up -d

# For CPU only:
docker compose -f docker/docker-compose.cpu.yml up -d
```

### Step 6: Verify It's Working

```bash
docker logs kanyo-detection --tail 50 -f
```

You should see:
```
INFO | Resolving stream URL...
INFO | ✅ Connected to stream
INFO | Frame 100: No detection
INFO | Frame 200: bird detected (confidence: 0.67)
```

Press `Ctrl+C` to stop following logs.

---

## Option B: Bare Metal (No Docker)

### Step 1: Clone and Set Up Environment

```bash
git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git
cd kanyo-contemplating-falcons-dev

python3.11 -m venv venv
source venv/bin/activate
```

### Step 2: Install Dependencies

```bash
pip install -r requirements-base.txt

# Choose ONE ML backend:
pip install -r docker/requirements-ml-cpu.txt      # CPU (slowest)
# OR
pip install -r docker/requirements-ml-vaapi.txt    # Intel iGPU
# OR
pip install -r docker/requirements-ml-cuda.txt     # NVIDIA GPU
```

### Step 3: Create Configuration

```bash
cp configs/config.template.yaml config.yaml
mkdir -p clips logs
```

Edit `config.yaml` with your stream URL.

### Step 4: Run Kanyo

```bash
python -m kanyo.detection.buffer_monitor config.yaml
```

### Step 5: Run as a Service (Optional)

Create `/etc/systemd/system/kanyo.service`:

```ini
[Unit]
Description=Kanyo Falcon Detection
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/kanyo-contemplating-falcons-dev
Environment="TELEGRAM_BOT_TOKEN=your_token"
ExecStart=/path/to/kanyo-contemplating-falcons-dev/venv/bin/python -m kanyo.detection.buffer_monitor config.yaml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable kanyo
sudo systemctl start kanyo
sudo journalctl -u kanyo -f
```

---

## Telegram Notifications (Optional)

### Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### Create a Channel

1. Create a new **Channel** in Telegram (not a group)
2. Make it **Public** with a username (e.g., `@my_falcon_alerts`)
3. Add your bot as Administrator with **Post Messages** permission

### Configure Kanyo

**For Docker:** Create a `.env` file:
```bash
echo "TELEGRAM_BOT_TOKEN=your_bot_token_here" > .env
```

**For bare metal:** Export the variable:
```bash
export TELEGRAM_BOT_TOKEN=your_bot_token_here
```

Update `config.yaml`:
```yaml
telegram_enabled: true
telegram_channel: "@my_falcon_alerts"
```

Restart Kanyo.

---

## Configuration Reference

Key settings in `config.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `video_source` | — | YouTube stream URL (required) |
| `timezone` | UTC | Stream's local timezone |
| `detection_confidence` | 0.4 | Minimum confidence (0.0–1.0) |
| `detection_confidence_ir` | 0.2 | Threshold for IR/night cameras |
| `frame_interval` | 3 | Process every Nth frame |
| `exit_timeout` | 90 | Seconds without detection = departed |
| `roosting_threshold` | 1800 | Seconds (30 min) before roosting alert |
| `arrival_confirmation_seconds` | 10 | Window to confirm arrival |
| `arrival_confirmation_ratio` | 0.3 | % of frames needed to confirm |

See `configs/config.template.yaml` for the full list.

---

## Troubleshooting

### "YouTube precondition failed"

YouTube changed their API. Rebuild with latest yt-dlp:

```bash
# Docker
docker compose -f docker/docker-compose.nvidia.yml build --no-cache
docker compose -f docker/docker-compose.nvidia.yml up -d

# Bare metal
pip install --upgrade yt-dlp
```

### No detections but bird is visible

- Lower `detection_confidence` (try 0.3)
- For IR/night cameras, lower `detection_confidence_ir` (try 0.15)
- Ensure `detect_any_animal: true` (YOLO sometimes misclassifies birds)

### Too many false arrivals

- Raise `detection_confidence` (try 0.5)
- Increase `arrival_confirmation_ratio` (try 0.4)
- Increase `arrival_confirmation_seconds` (try 15)

### Telegram not sending

1. Verify bot token is set correctly
2. Verify bot is admin of channel with Post Messages permission
3. Check logs: `docker logs kanyo-detection | grep -i telegram`

### High CPU usage

- Increase `frame_interval` (try 5 or 10)
- Enable hardware encoding: `clip_hardware_encoding: true`
- Use Docker with GPU support

### Check hardware encoding

```bash
# See what encoders are available
python -c "from kanyo.utils.encoder import detect_hardware_encoder; print(detect_hardware_encoder(verbose=True))"
```

### Import errors (bare metal)

```bash
source venv/bin/activate
python --version  # Needs 3.11+
pip install -r requirements-base.txt
```

### Permission errors

```bash
mkdir -p clips logs
chmod 755 clips logs
# For Docker: ensure directories are owned by UID 1000
sudo chown -R 1000:1000 clips logs
```

---

## Quick Commands

```bash
# Start (Docker)
docker compose -f docker/docker-compose.nvidia.yml up -d

# Stop (Docker)
docker compose -f docker/docker-compose.nvidia.yml down

# View logs (Docker)
docker logs kanyo-detection --tail 100 -f

# Restart after config change (Docker)
docker compose -f docker/docker-compose.nvidia.yml restart

# Rebuild (after code changes)
docker compose -f docker/docker-compose.nvidia.yml build --no-cache
docker compose -f docker/docker-compose.nvidia.yml up -d

# Check clips
ls -la clips/$(date +%Y-%m-%d)/

# Disk usage
du -sh clips/
```

---

## Next Steps

- **[docs/adding-streams.md](docs/adding-streams.md)** — Add more camera streams
- **[docs/sensing-logic.md](docs/sensing-logic.md)** — Understand how detection works
- **[docker/DOCKER-DEPLOYMENT.md](docker/DOCKER-DEPLOYMENT.md)** — Advanced Docker deployment with ZFS
