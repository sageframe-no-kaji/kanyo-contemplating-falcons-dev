# Kanyo (観鷹)
**Contemplating Falcons**

Real-time falcon detection and event tracking for live camera streams. Automatically captures video clips when falcons arrive or depart, tracks roosting behavior, and sends notifications via Telegram.

## What It Does

- 🦅 **Detects falcons** in live YouTube streams using YOLOv8
- 📹 **Captures video clips** of arrivals and departures
- 🔔 **Sends notifications** via Telegram when falcons are spotted
- 📊 **Tracks behavior** with state machine (absent → visiting → roosting → departed)
- 🕐 **Generates timelines** with thumbnails and event logs

## Origin Story

Born from a conversation with Claudia Goldin (Nobel laureate in Economics) on a flight to New York, where she expressed interest in having the live feed automatically mark timestamps when the peregrines are actually in frame.

---

## Quick Start

### Docker (Recommended)

```bash
# 1. Create project directory
mkdir kanyo && cd kanyo

# 2. Download docker-compose file (choose one):
#    CPU:    docker-compose.cpu.yml
#    Intel:  docker-compose.vaapi.yml
#    NVIDIA: docker-compose.nvidia.yml

curl -O https://raw.githubusercontent.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/main/docker/docker-compose.nvidia.yml
mv docker-compose.nvidia.yml docker-compose.yml

# 3. Download config template
curl -O https://raw.githubusercontent.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/main/configs/config.template.yaml
mv config.template.yaml config.yaml
# Edit config.yaml with your stream URL and settings

# 4. Create directories for persistent data
mkdir -p clips logs

# 5. Create .env file (if using Telegram)
echo "TELEGRAM_BOT_TOKEN=your_token_here" > .env

# 6. Start
docker compose up -d
```

See **[docker/DOCKER-DEPLOYMENT.md](docker/DOCKER-DEPLOYMENT.md)** for multi-stream and ZFS deployment.

### Local Development

```bash
# Clone repository
git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git
cd kanyo-contemplating-falcons-dev

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Run detection on a stream
python -m kanyo.detection.buffer_monitor config.yaml
```

---

## Project Structure

```
kanyo-contemplating-falcons-dev/
├── src/kanyo/               # Main package
│   ├── detection/           # Video capture, detection, state machine
│   ├── generation/          # Clip extraction, site generation
│   └── utils/               # Config, logging, notifications, encoding
├── tests/                   # Test suite
├── configs/                 # Configuration templates
│   └── config.template.yaml # Documented config template
├── docker/                  # Docker deployment files
│   ├── Dockerfile.cpu       # Pure CPU variant
│   ├── Dockerfile.vaapi     # Intel iGPU + OpenVINO
│   ├── Dockerfile.nvidia    # NVIDIA CUDA 12.1
│   └── docker-compose.*.yml # Deployment configs
├── scripts/                 # Build, deploy, and utility scripts
├── docs/                    # Architecture and design documentation
└── devlog/                  # Development journal (hos)
```

---

## How It Works

### State Machine

```
ABSENT ──────► VISITING ──────► ROOSTING
   ▲              │                 │
   │              │                 │
   └──────────────┴─────────────────┘
              (exit_timeout)
```

| State | Meaning | Exit Condition |
|-------|---------|----------------|
| **ABSENT** | No falcon | Bird detected → VISITING |
| **VISITING** | Bird present < 30 min | Gone 90s → DEPARTED, or stays 30 min → ROOSTING |
| **ROOSTING** | Bird present > 30 min | Gone 90s → DEPARTED |

ROOSTING triggers a notification but uses the same exit timeout as VISITING.

### Clips Created

| Event | Clip | Timing |
|-------|------|--------|
| **Arrival** | `falcon_HHMMSS_arrival.mp4` | 15s before + 30s after detection |
| **Departure** | `falcon_HHMMSS_departure.mp4` | 60s before + 30s after last detection |
| **Full Visit** | `falcon_HHMMSS_visit.mp4` | Entire visit recording |

---

## Configuration

See **[configs/config.template.yaml](configs/config.template.yaml)** for all options.

Minimal config:
```yaml
video_source: "https://www.youtube.com/watch?v=..."
detection_confidence: 0.35
frame_interval: 2
telegram_enabled: false
```

Key parameters:

| Setting | Description | Default |
|---------|-------------|---------|
| `video_source` | YouTube stream URL | (required) |
| `detection_confidence` | Detection threshold (0.0-1.0) | 0.35 |
| `frame_interval` | Frames to skip between detections | 2 |
| `exit_timeout` | Seconds absent before departure | 90 |
| `roosting_threshold` | Seconds before roosting notification | 1800 |
| `telegram_enabled` | Send notifications | false |
| `timezone` | Timezone offset for logs/filenames | "+00:00" |

---

## Deployment Options

| Method | Best For | Hardware |
|--------|----------|----------|
| **[Docker](docker/DOCKER-DEPLOYMENT.md)** | Production, multi-stream | CPU, Intel iGPU, NVIDIA GPU |
| **Local** | Development, testing | Any |

Three Docker image variants:
- **`:cpu`** - Pure CPU (PyTorch CPU, no GPU)
- **`:vaapi`** - Intel iGPU (PyTorch + OpenVINO)
- **`:nvidia`** - NVIDIA GPU (PyTorch + CUDA 12.1)

---

## Documentation

- **[docker/DOCKER-DEPLOYMENT.md](docker/DOCKER-DEPLOYMENT.md)** - Complete Docker guide
- **[docs/sensing-logic.md](docs/sensing-logic.md)** - Detection and state machine details
- **[devlog/](devlog/)** - Development journal

---

## Technology Stack

- **Python 3.11+** - Core language
- **YOLOv8** - Object detection (ultralytics)
- **OpenCV** - Video/frame processing
- **yt-dlp** - YouTube stream capture
- **FFmpeg** - Video encoding with hardware acceleration
- **Docker** - Containerized deployment
- **Telegram Bot API** - Notifications

---

## Development

```bash
# Activate environment
source venv/bin/activate

# Run tests
pytest

# Format code
black src/ tests/
isort src/ tests/

# Type check
mypy src/kanyo/
```

---

## YouTube Stream Recovery

### How Kanyo Handles YouTube API Changes

YouTube frequently changes their API, which can break stream capture. Kanyo handles this automatically:

1. **Build-time protection**: yt-dlp is upgraded at container build time
2. **Runtime fallback**: If YouTube returns "Precondition check failed", Kanyo switches to an alternate API client (`android_creator`) and retries once
3. **Cooldown on failure**: If fallback also fails, Kanyo waits 5 minutes before retrying to avoid rate limiting

### Log Messages

| Message | Meaning |
|---------|---------|
| `YouTube precondition failed; retrying with alternate yt-dlp client` | Normal recovery, trying fallback |
| `YouTube stream still failing after fallback; entering cooldown` | Both methods failed, waiting 5 min |
| `✅ Connected to stream` | Recovery complete |

### If Streams Stay Down

If streams fail persistently after multiple cooldown cycles:

1. Check if the YouTube stream is actually live
2. Rebuild the container to get latest yt-dlp: `docker compose build --no-cache`
3. Check yt-dlp GitHub issues for known YouTube breakages

---

## License

MIT

## Acknowledgments

- Claudia Goldin - For the inspiration
- Memorial Hall Falcon Cam - For the falcons
- Anthropic - For Claude (development assistant)
