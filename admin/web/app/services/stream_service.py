"""Stream discovery and management service."""

from typing import Optional

import yaml

from app.config import settings


def discover_streams() -> list[dict]:
    """
    Scan /data/*/config.yaml and return stream info.

    Returns:
        List of stream dictionaries with id, name, container_name, etc.
    """
    streams = []
    data_path = settings.DATA_PATH

    if not data_path.exists():
        return []

    # Scan each directory in /data/
    for stream_dir in data_path.iterdir():
        if not stream_dir.is_dir():
            continue

        config_path = stream_dir / "config.yaml"
        if not config_path.exists():
            continue

        try:
            # Read config
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)

            stream_id = stream_dir.name
            stream_info = {
                "id": stream_id,
                "name": config.get("stream_name", stream_id),
                "container_name": f"kanyo-{stream_id}-gpu",
                "config_path": str(config_path),
                "clips_path": str(stream_dir / "clips"),
                "video_source": config.get("video_source", ""),
                "timezone": config.get("timezone", "UTC"),
            }
            streams.append(stream_info)
        except Exception as e:
            # Skip streams with invalid config
            print(f"Error reading config for {stream_dir.name}: {e}")
            continue

    return streams


def get_stream(stream_id: str) -> Optional[dict]:
    """
    Get single stream by ID.

    Args:
        stream_id: Stream identifier (directory name)

    Returns:
        Stream dict or None if not found
    """
    streams = discover_streams()
    for stream in streams:
        if stream["id"] == stream_id:
            return stream
    return None
