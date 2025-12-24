"""
Output utilities for generating paths, thumbnails, and formatting.

Shared utilities for file path generation and output formatting.
"""

from datetime import datetime
from pathlib import Path

import cv2

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


def get_output_path(base_dir: str, timestamp: datetime, event_type: str, extension: str) -> Path:
    """
    Generate date-organized output path for clips/thumbnails.

    Args:
        base_dir: Base directory (e.g., 'clips')
        timestamp: Event timestamp
        event_type: 'arrival', 'departure', 'visit', 'final', etc.
        extension: 'mp4' or 'jpg'

    Returns:
        Path like: clips/2025-12-24/falcon_143025_arrival.mp4
    """
    date_dir = Path(base_dir) / timestamp.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    filename = f"falcon_{timestamp.strftime('%H%M%S')}_{event_type}.{extension}"
    return date_dir / filename


def save_thumbnail(frame_data, base_dir: str, timestamp: datetime, event_type: str) -> str:
    """
    Save frame as timestamped thumbnail.

    Args:
        frame_data: OpenCV frame (numpy array)
        base_dir: Base directory for output
        timestamp: Event timestamp
        event_type: Type of event ('arrival', 'departure', etc.)

    Returns:
        String path to saved thumbnail
    """
    path = get_output_path(base_dir, timestamp, event_type, "jpg")
    cv2.imwrite(str(path), frame_data)
    logger.debug(f"Saved thumbnail: {path}")
    return str(path)


def format_duration(seconds: float) -> str:
    """
    Format duration as human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "5m 23s" or "2h 15m"

    Examples:
        >>> format_duration(45)
        '45s'
        >>> format_duration(125)
        '2m 5s'
        >>> format_duration(3665)
        '1h 1m'
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s" if secs > 0 else f"{minutes}m"
    else:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
