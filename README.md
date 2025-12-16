
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

```bash
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
```

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
