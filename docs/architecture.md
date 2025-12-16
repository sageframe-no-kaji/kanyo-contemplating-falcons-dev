# Kanyo Architecture

## System Overview

```
YouTube Stream → yt-dlp → Video Segments
                            ↓
                    Frame Extraction
                            ↓
                    YOLOv8 Detection
                            ↓
                    Event Detection
                            ↓
                    JSON + Thumbnails
                            ↓
                    Static Site Generator
                            ↓
                    Cloudflare Pages
```

## Components

### Detection Pipeline
- **capture.py** - Download stream segments with yt-dlp
- **detect.py** - Run YOLOv8 on frames, return detections
- **events.py** - Process detections into events (enter/exit/movement)

### Generation Pipeline
- **site_generator.py** - Generate HTML from detection JSON

### Utilities
- **config.py** - Configuration management
- **logger.py** - Logging setup

## Data Flow

1. **Input:** YouTube live stream URL
2. **Capture:** Download recent segments (e.g., last 2 hours)
3. **Process:** Extract frames at interval (e.g., every 30 frames)
4. **Detect:** Run YOLOv8, filter for birds
5. **Events:** Identify enter/exit/movement events
6. **Output:** JSON with timestamps + thumbnail images
7. **Generate:** Create static HTML timeline
8. **Deploy:** Push to Cloudflare Pages

## Configuration

See `config.yaml` for all settings.

Key parameters:
- `detection.confidence` - Threshold for bird detection
- `detection.frame_interval` - How often to check frames
- `events.enter_threshold` - Frames to confirm entrance

## Deployment

### Local Development
```bash
python scripts/run_detection.py --video sample.mp4
```

### Automated (GitHub Actions)
- Runs hourly
- Downloads last 2 hours of stream
- Processes and deploys to Cloudflare

## Testing

```bash
pytest                          # Run all tests
pytest tests/test_detection.py  # Specific test
pytest --cov                    # With coverage
```
```

### Create Data Model Doc

**In `docs/data-model.md`:**
```markdown
# Kanyo Data Model

## Detection Event

```json
{
  "timestamp": "2024-12-15T14:23:45Z",
  "youtube_time": "3h45m23s",
  "event_type": "falcon_enters",
  "confidence": 0.94,
  "thumbnail": "thumbs/20241215_142345.jpg",
  "falcon_count": 1,
  "bbox": [120, 340, 280, 520],
  "metadata": {
    "video_segment": "segment_20241215_1200.mp4",
    "frame_number": 12450
  }
}
```

## Event Types

- **falcon_enters** - Bird appears after absence (N frames)
- **falcon_exits** - Bird disappears after presence (N frames)
- **movement_after_stasis** - Movement after 5+ minutes still
- **falcon_count_change** - Number of visible falcons changes
- **significant_activity** - High motion detected

## Detection File

**File:** `site/data/detections.json`

```json
{
  "generated_at": "2024-12-15T15:00:00Z",
  "stream_url": "https://youtube.com/watch?v=...",
  "detection_config": {
    "model": "yolov8n.pt",
    "confidence": 0.6
  },
  "events": [
    { /* event 1 */ },
    { /* event 2 */ }
  ],
  "summary": {
    "total_events": 45,
    "falcon_enters": 12,
    "falcon_exits": 12,
    "movement_events": 21,
    "time_range": {
      "start": "2024-12-15T08:00:00Z",
      "end": "2024-12-15T15:00:00Z"
    }
  }
}
```

## Configuration Schema

See `config.yaml` - all settings documented inline.

### Update Main README

**Edit `README.md` to be comprehensive:**

# Kanyo (観鷹)
**Contemplating Falcons**

Automated detection and timeline generation for the Memorial Hall Peregrine Falcon cam at Harvard.

## Origin Story

Born from a conversation with Claudia Goldin (Nobel laureate in Economics) on a flight to New York, where she expressed interest in having the live feed automatically mark timestamps when the peregrines are actually in frame.

## Project Status

🚧 **In Development** - Ho 1 complete (project structure established)

**Current Phase:** Foundation
**Next:** Falcon detection implementation (Ho 2)

## Quick Start

# Clone and setup
git clone https://github.com/YOUR_USERNAME/kanyo.git
cd kanyo

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Test configuration
python scripts/test_config.py

## Project Structure

```
kanyo/
├── src/kanyo/           # Main package
│   ├── detection/       # Video capture and falcon detection
│   ├── generation/      # Static site generation
│   └── utils/           # Configuration and logging
├── tests/               # Test suite
├── scripts/             # Utility scripts
├── docs/                # Architecture and design docs
├── devlog/              # Development journal (hos)
└── site/                # Generated static site
```

## Documentation

- [Architecture](docs/architecture.md) - System design and data flow
- [Data Model](docs/data-model.md) - Event structure and schemas
- [Development Log](devlog/) - Ho-by-ho progress

## Technology Stack

- **Python 3.10+** - Core language
- **YOLOv8** - Object detection
- **OpenCV** - Video processing
- **yt-dlp** - Stream capture
- **GitHub Actions** - Automation
- **Cloudflare Pages** - Hosting

## Development

### Activate environment
source venv/bin/activate

### Format code
black src/ tests/
isort src/ tests/

### Check code quality
flake8 src/ tests/
mypy src/kanyo/

### Run tests with coverage
pytest --cov

## Configuration

See `config.yaml` for all settings. Key parameters:

- **video.source** - YouTube stream URL
- **detection.confidence** - Detection threshold (0.0-1.0)
- **detection.model** - YOLOv8 model (n/s/m)
- **events.enter_threshold** - Frames to confirm entrance

## Roadmap

- [x] Ho 0: Project planning
- [x] Ho 0.5: Tool mastery
- [x] Ho 1: Project structure ← **You are here**
- [ ] Ho 2: Falcon detection
- [ ] Ho 3: Event detection
- [ ] Ho 4: Stream capture
- [ ] Ho 5: Pipeline assembly
- [ ] Ho 6: GitHub Actions automation
- [ ] Ho 7: Static site generation
- [ ] Ho 8: Cloudflare deployment
- [ ] Ho 9: User tagging system
- [ ] Ho 10-11: Polish and launch

## Contributing

This is a personal learning project, but feedback and suggestions are welcome!

## License

MIT

## Acknowledgments

- Claudia Goldin - For the inspiration
- Memorial Hall Falcon Cam - For the falcons
- Anthropic - For Claude (development assistant)
