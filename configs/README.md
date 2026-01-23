# Configuration Templates

This directory contains configuration templates for Kanyo streams.

## Files

- **config.template.yaml** — Full template with all options documented. Copy this to create your stream config.

## Usage
```bash
# Copy template to your stream directory
cp config.template.yaml /opt/services/kanyo-mystream/config.yaml

# Edit with your stream details
nano /opt/services/kanyo-mystream/config.yaml
```

## Required Fields

- `video_source` — YouTube stream URL
- `stream_name` — Display name for admin UI
- `timezone` — IANA timezone (e.g., "America/New_York")

## See Also

- [QUICKSTART.md](../QUICKSTART.md) — Getting started
- [docs/adding-streams.md](../docs/adding-streams.md) — Multi-stream setup
