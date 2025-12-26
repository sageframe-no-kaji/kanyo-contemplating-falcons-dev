# Kanyo Deployment Guide

Kanyo is a falcon detection system for live camera streams.

## Deployment Options

### Docker Deployment (Recommended)

See **[docker/DOCKER-DEPLOYMENT.md](docker/DOCKER-DEPLOYMENT.md)** for complete Docker-based deployment with:
- Pre-built images (CPU, Intel iGPU, NVIDIA GPU)
- Persistent storage for clips and logs
- Multi-stream support
- Optional ZFS setup with snapshots and quotas

### Bare Metal Deployment

For running Kanyo directly on your system without Docker.

#### Requirements

- Python 3.11+
- ffmpeg with hardware encoding support (optional but recommended)
- Git

#### Quick Start

```bash
# 1. Clone repository
git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git
cd kanyo-contemplating-falcons-dev

# 2. Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements-base.txt

# Choose ONE ML backend:
pip install -r docker/requirements-ml-cpu.txt      # Pure CPU (slowest, most portable)
# OR
pip install -r docker/requirements-ml-vaapi.txt    # Intel iGPU + OpenVINO (medium speed)
# OR  
pip install -r docker/requirements-ml-cuda.txt     # NVIDIA GPU (fastest, requires CUDA 12.1)

# 4. Create configuration
cp configs/config.template.yaml config.yaml
# Edit config.yaml with your camera stream URL and settings

# 5. Create directories for output
mkdir -p clips logs

# 6. Set environment variables (optional)
export TELEGRAM_BOT_TOKEN="your_token_here"  # If using Telegram notifications

# 7. Run Kanyo
python -m kanyo.detection.realtime_monitor config.yaml
```

#### Directory Structure

```
kanyo-contemplating-falcons-dev/
├── config.yaml              # Your stream configuration
├── clips/                   # Generated video clips (grows over time)
│   └── YYYY-MM-DD/
│       ├── falcon_*.mp4
│       ├── falcon_*.jpg
│       └── events_*.json
├── logs/                    # Application logs
│   └── kanyo.log
└── venv/                    # Python virtual environment
```

#### Running Multiple Streams

For multiple camera streams on bare metal, run separate processes with different configs:

```bash
# Terminal 1
python -m kanyo.detection.realtime_monitor config-harvard.yaml

# Terminal 2  
python -m kanyo.detection.realtime_monitor config-nsw.yaml
```

Or use a process manager like `systemd`, `supervisord`, or `tmux`.

#### systemd Service Example

```ini
# /etc/systemd/system/kanyo-harvard.service
[Unit]
Description=Kanyo Falcon Detection - Harvard
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/kanyo-contemplating-falcons-dev
Environment="TELEGRAM_BOT_TOKEN=your_token"
ExecStart=/home/youruser/kanyo-contemplating-falcons-dev/venv/bin/python -m kanyo.detection.realtime_monitor config-harvard.yaml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start service
sudo systemctl enable kanyo-harvard
sudo systemctl start kanyo-harvard
sudo systemctl status kanyo-harvard

# View logs
sudo journalctl -u kanyo-harvard -f
```

#### Hardware Encoding

Kanyo auto-detects hardware encoders for video processing:

- **NVIDIA GPU**: NVENC (h264_nvenc)
- **Intel iGPU**: VAAPI (h264_vaapi)
- **Apple Silicon**: VideoToolbox (h264_videotoolbox)
- **Fallback**: Software encoding (libx264)

Check detected encoder:
```bash
python -c "from kanyo.utils.encoder import detect_hardware_encoder; print(detect_hardware_encoder(verbose=True))"
```

#### Troubleshooting

**Import errors:**
```bash
# Make sure you're in the virtual environment
source venv/bin/activate

# Check Python version (needs 3.11+)
python --version

# Reinstall dependencies
pip install -r requirements-base.txt
pip install -r docker/requirements-ml-cpu.txt  # or vaapi/cuda
```

**Permission errors on clips/logs:**
```bash
mkdir -p clips logs
chmod 755 clips logs
```

**High CPU/memory usage:**
- Increase `frame_interval` in config.yaml (process fewer frames)
- Lower `detection_confidence` threshold
- Use hardware encoding (see above)
- Consider Docker deployment with resource limits

**YouTube stream issues:**
```bash
# Update yt-dlp
pip install --upgrade yt-dlp

# Test stream URL manually
yt-dlp -F "your_youtube_url"
```

---

## Configuration

See **[configs/config.template.yaml](configs/config.template.yaml)** for all available options with documentation.

Minimal working config:
```yaml
video_source: "https://www.youtube.com/watch?v=..."
detection_confidence: 0.35
frame_interval: 2
telegram_enabled: false
```

---

## Choosing a Deployment Method

| Method | Best For | Pros | Cons |
|--------|----------|------|------|
| **Docker** | Production, multiple streams | Isolated, portable, easy updates | Requires Docker knowledge |
| **Bare Metal** | Development, single stream | Direct access, easier debugging | Manual dependency management |

For production deployments with multiple streams, **Docker is strongly recommended**. See [docker/DOCKER-DEPLOYMENT.md](docker/DOCKER-DEPLOYMENT.md).
