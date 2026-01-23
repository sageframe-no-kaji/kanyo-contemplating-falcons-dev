# Kanyo (観鷹)

### _Contemplating Falcons_

An open-source system for monitoring wildlife cameras with computer vision. Kanyo watches live YouTube streams, detects when birds arrive and depart, captures video clips of each visit, and sends real-time notifications.

**Live Demo:** [kanyo.sageframe.net](https://kanyo.sageframe.net)

![Kanyo Viewer](docs/images/viewer-screenshot.png)

---

## Origin Story

This project began on a flight to New York in December 2024. I found myself seated next to a falcon enthusiast, who mentioned her connection to the peregrine falcon nest cam atop Memorial Hall at Harvard. She wondered aloud whether someone could build a tool to automatically mark timestamps when the falcons were actually in frame—so enthusiasts wouldn't have to scrub through hours of empty nest footage.

It turns out that someone was me.

What started as a simple notification system grew into a full monitoring platform. The name **Kanyo** (観鷹) combines the Japanese characters for "contemplating" and "falcon"—a nod to both the meditative act of watching these birds and the computational attention the system pays to every frame.

---

## What It Does

- **Detects birds** in live YouTube streams using YOLOv8 computer vision
- **Tracks behavior** with a debounced state machine (no more false "arrived/departed" spam)
- **Records video clips** of arrivals, departures, and complete visits
- **Sends notifications** via Telegram when something happens
- **Serves a web interface** for browsing events, watching clips, and viewing live streams

### The Detection Problem

Naive detection systems trigger on every frame: "FALCON DETECTED... NOT DETECTED... DETECTED..." — generating hundreds of false events when a bird simply moves within the frame. Kanyo uses a state machine with configurable timeouts to produce clean, meaningful events:

```
10:00:01 | 🦅 ARRIVED
10:30:00 | 🏠 ROOSTING (settled in)
13:00:00 | 👋 DEPARTED (3 hour visit)
```

One arrival. One departure. Three hours of presence tracked correctly.

---

## Project Structure

Kanyo is split across two repositories:

| Repository                                                                                              | Purpose                                         |
| ------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| [kanyo-contemplating-falcons-dev](https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev) | Detection engine, clip recording, notifications |
| [kanyo-viewer](https://github.com/sageframe-no-kaji/kanyo-viewer)                                       | Web interface for browsing streams and clips    |

Both are designed to run in Docker containers. The detection engine processes streams 24/7; the viewer serves the web interface.

---

## Quick Start

Get a single stream running in under 10 minutes:

```bash
git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git
cd kanyo-contemplating-falcons-dev
cp configs/config.template.yaml config.yaml
# Edit config.yaml with your YouTube stream URL

mkdir -p clips logs
docker compose -f docker/docker-compose.nvidia.yml up -d
```

See **[QUICKSTART.md](QUICKSTART.md)** for the full walkthrough, including Telegram setup and hardware options.

---

## Documentation

| Document                                         | Description                           |
| ------------------------------------------------ | ------------------------------------- |
| [QUICKSTART.md](QUICKSTART.md)                   | Get running in 10 minutes             |
| [docs/adding-streams.md](docs/adding-streams.md) | Multi-stream deployment guide         |
| [docs/sensing-logic.md](docs/sensing-logic.md)   | How the detection state machine works |

---

## Technology

**Detection Engine:**

- Python 3.11
- YOLOv8 (ultralytics) for object detection
- OpenCV for video processing
- yt-dlp for YouTube stream capture
- FFmpeg for clip encoding (with hardware acceleration support)

**Viewer:**

- FastAPI backend
- React + Vite + Tailwind CSS frontend
- HTMX for admin interface

**Deployment:**

- Docker with NVIDIA GPU support (also CPU and Intel iGPU variants)
- Cloudflare Tunnels for public access

---

## Development

```bash
# Clone and set up
git clone https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git
cd kanyo-contemplating-falcons-dev
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt

# Run tests
pytest

# Run detection locally
python -m kanyo.detection.buffer_monitor config.yaml
```

The project has 114 passing tests covering the detection logic, state machine, and clip extraction.

---

## Status

Kanyo is in active use, monitoring falcon cams and sending thousands of notifications. The core detection system is stable; the viewer and admin interfaces continue to evolve.

**Current capabilities:**

- Multi-stream monitoring with independent detection containers
- Arrival confirmation to filter single-frame false positives
- Automatic YouTube stream reconnection
- Hardware-accelerated video encoding (NVIDIA, Intel, CPU fallback)
- Timezone-aware event logging and display
- Mobile-responsive viewer interface

**On the roadmap:**

- Auto-discovery of streams (eliminate manual config editing)
- Research-focused features (annotation, data export)
- Community features for falcon enthusiasts

---

## Contributing

Issues and pull requests are welcome. If you're interested in:

- **Adding support for new camera types** — the detection is general-purpose
- **Improving the viewer UI** — React/Tailwind skills appreciated
- **Research applications** — I'd love to hear from ornithologists

---

## Acknowledgments

- **Claudia Goldin** — For the spark that started this project
- **Memorial Hall Falcon Cam** — For the falcons themselves
- **Anthropic** — Claude has been an invaluable development partner
- **The falcon cam community** — Enthusiasts who watch and report

---

## License

MIT
