# Kanyo (観鷹)
**Contemplating Falcons**

Real-time falcon detection and event tracking for live camera streams. Automatically captures video clips when falcons arrive or depart, tracks roosting behavior, and sends notifications via Telegram.

## What It Does

- 🦅 **Detects falcons** in live YouTube streams using YOLOv8
- 📹 **Captures video clips** of arrivals, departures, and activity
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
#    CPU:    https://raw.githubusercontent.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/main/docker/docker-compose.example.yml
#    Intel:  https://raw.githubusercontent.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/main/docker/docker-compose.vaapi.yml
#    NVIDIA: https://raw.githubusercontent.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev/main/docker/docker-compose.nvidia.yml

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

### Local Development (Clone Required)

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
python -m kanyo.detection.realtime_monitor config.yaml
```

---

## Project Structure

```
kanyo-contemplating-falcons-dev/
├── src/kanyo/               # Main package
│   ├── detection/           # Video capture, detection, state machine
│   ├── generation/          # Clip extraction, site generation
│   └── utils/               # Config, logging, notifications, encoding
├── tests/                   # Test suite (115 tests)
├── configs/                 # Configuration templates and examples
│   ├── config.template.yaml # Documented config template
│   └── kanyo-stream-config.example.yaml
├── docker/                  # Docker deployment files
│   ├── Dockerfile.cpu       # Pure CPU variant
│   ├── Dockerfile.vaapi     # Intel iGPU + OpenVINO
│   ├── Dockerfile.nvidia    # NVIDIA CUDA 12.1
│   ├── docker-compose.*.yml # Deployment configs
│   └── requirements-ml-*.txt # ML dependencies per variant
├── scripts/                 # Build, deploy, and utility scripts
│   └── INDEX.md             # Script documentation
├── docs/                    # Architecture and design documentation
└── devlog/                  # Development journal (hos)
```

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
| `frame_interval` | Seconds between detection frames | 2 |
| `telegram_enabled` | Send notifications | false |
| `telegram_channel` | Telegram channel ID | - |

---

## Deployment Options

| Method | Best For | Hardware |
|--------|----------|----------|
| **[Docker](docker/DOCKER-DEPLOYMENT.md)** | Production, multi-stream | CPU, Intel iGPU, NVIDIA GPU |
| **[Bare Metal](DEPLOYMENT.md)** | Development, single stream | Any |

Three Docker image variants:
- **`:cpu`** - Pure CPU (PyTorch CPU, no GPU)
- **`:vaapi`** - Intel iGPU (PyTorch + OpenVINO)
- **`:nvidia`** - NVIDIA GPU (PyTorch + CUDA 12.1)

---

## Documentation

- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Bare metal and Docker deployment
- **[docker/DOCKER-DEPLOYMENT.md](docker/DOCKER-DEPLOYMENT.md)** - Complete Docker guide with ZFS
- **[docs/architecture.md](docs/architecture.md)** - System design and data flow
- **[docs/state-detection.md](docs/state-detection.md)** - Falcon state machine
- **[scripts/INDEX.md](scripts/INDEX.md)** - Build and deploy script reference
- **[devlog/](devlog/)** - Development journal (ho-by-ho progress)

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

# Run tests with coverage
pytest --cov

# Format code
black src/ tests/
isort src/ tests/

# Check code quality
flake8 src/ tests/
mypy src/kanyo/
```

---

## Roadmap

- [x] Ho 0-1: Project foundation
- [x] Ho 2: Falcon detection (YOLOv8)
- [x] Ho 3: Live detection & notifications
- [x] Ho 4: Docker deployment
- [x] Ho 5: Production verification
- [ ] Ho 6: Static site generation
- [ ] Ho 7: User tagging system
- [ ] Ho 8: Multi-camera dashboard

---

## License

MIT

## Acknowledgments

- Claudia Goldin - For the inspiration
- Memorial Hall Falcon Cam - For the falcons
- Anthropic - For Claude (development assistant)
