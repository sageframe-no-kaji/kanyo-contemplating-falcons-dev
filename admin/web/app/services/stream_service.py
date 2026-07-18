"""Stream discovery and management service.

Streams are discovered from subdirectories of DATA_PATH (/data) that contain
a config.yaml — the same pattern the viewer uses. Two layouts are supported
(issue #5):

- Parent mount (preferred): the whole services root is mounted once
  (`/opt/services:/data`), so streams appear as /data/kanyo-<id>/. The
  "kanyo-" prefix is stripped to form the stream id, and known non-stream
  service dirs (admin, code, nvidia, viewer) are excluded. New streams
  appear without any docker-compose changes.

- Per-stream mounts (legacy): each stream mounted individually as
  /data/<id>/. Still recognized, so existing deployments and dev setups
  keep working unchanged.

Directories without a config.yaml are ignored; malformed configs are logged
and skipped.
"""

from pathlib import Path
from typing import Optional

import yaml
from app.config import settings

# Service directories under the parent mount that are never streams, even if
# a config.yaml shows up in them (e.g. a dev checkout inside kanyo-code).
RESERVED_DIR_NAMES = {"kanyo-admin", "kanyo-code", "kanyo-nvidia", "kanyo-viewer"}

KANYO_PREFIX = "kanyo-"


def _stream_id_for(stream_dir: Path) -> str:
    """Stream id for a data directory: dir name with the kanyo- prefix stripped."""
    name = stream_dir.name
    if name.startswith(KANYO_PREFIX):
        return name[len(KANYO_PREFIX) :]
    return name


def resolve_stream_dir(stream_id: str) -> Optional[Path]:
    """
    Resolve a stream id to its data directory, or None if unknown.

    Checks both layouts: /data/<id>/ (per-stream mount) and /data/kanyo-<id>/
    (parent mount). Only directories that carry a config.yaml and are not
    reserved service dirs qualify — this keeps file-serving endpoints from
    exposing non-stream dirs (e.g. kanyo-admin) under the parent mount.
    """
    for name in (stream_id, f"{KANYO_PREFIX}{stream_id}"):
        if name in RESERVED_DIR_NAMES:
            continue
        candidate = settings.DATA_PATH / name
        if candidate.is_dir() and (candidate / "config.yaml").exists():
            return candidate
    return None


def discover_streams() -> list[dict]:
    """
    Scan DATA_PATH for stream dirs (subdirs containing config.yaml).

    Returns:
        List of stream dictionaries with id, name, container_name, paths, etc.
    """
    streams = []
    seen_ids: set[str] = set()
    data_path = settings.DATA_PATH

    if not data_path.exists():
        return []

    for stream_dir in sorted(data_path.iterdir()):
        if not stream_dir.is_dir():
            continue
        if stream_dir.name in RESERVED_DIR_NAMES:
            continue

        config_path = stream_dir / "config.yaml"
        if not config_path.exists():
            continue

        stream_id = _stream_id_for(stream_dir)
        if stream_id in seen_ids:
            # Both /data/<id> and /data/kanyo-<id> present — first one wins.
            print(f"Duplicate stream id '{stream_id}' at {stream_dir}; skipping")
            continue

        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)

            stream_info = {
                "id": stream_id,
                "name": config.get("stream_name", stream_id),
                "container_name": f"kanyo-{stream_id}-gpu",
                "data_path": str(stream_dir),
                "config_path": str(config_path),
                "clips_path": str(stream_dir / "clips"),
                "video_source": config.get("video_source", ""),
                "timezone": config.get("timezone", "UTC"),
                "display_order": config.get("display", {}).get("order", 999),
            }
            streams.append(stream_info)
            seen_ids.add(stream_id)
        except Exception as e:
            # Skip streams with invalid config
            print(f"Error reading config for {stream_dir.name}: {e}")
            continue

    streams.sort(key=lambda s: (s["display_order"], s["id"]))
    return streams


def get_stream(stream_id: str) -> Optional[dict]:
    """
    Get single stream by ID.

    Args:
        stream_id: Stream identifier (directory name, without kanyo- prefix)

    Returns:
        Stream dict or None if not found
    """
    streams = discover_streams()
    for stream in streams:
        if stream["id"] == stream_id:
            return stream
    return None
