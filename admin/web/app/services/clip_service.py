"""Clip browsing and management service."""

from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import re


def list_clips(clips_path: str, date: str) -> list[dict]:
    """
    List clips for given date.

    Args:
        clips_path: Path to clips directory
        date: Date string (YYYY-MM-DD)

    Returns:
        List of clip dictionaries with filename, time, type, etc.
    """
    clips = []
    date_path = Path(clips_path) / date

    if not date_path.exists():
        return []

    # Pattern: falcon_HHMMSS_type.ext
    pattern = re.compile(r'falcon_(\d{6})_(\w+)\.(\w+)$')

    for clip_file in sorted(date_path.iterdir()):
        if not clip_file.is_file():
            continue

        match = pattern.match(clip_file.name)
        if not match:
            continue

        time_str, clip_type, ext = match.groups()

        # Parse time
        hour = time_str[:2]
        minute = time_str[2:4]
        second = time_str[4:6]
        time = f"{hour}:{minute}:{second}"

        # Determine if video or image
        is_video = ext.lower() in ['mp4', 'avi', 'mov', 'mkv']

        clips.append({
            "filename": clip_file.name,
            "time": time,
            "type": clip_type,
            "is_video": is_video,
            "size_bytes": clip_file.stat().st_size,
        })

    return clips


def get_latest_thumbnail(clips_path: str, stream_id: str) -> Optional[str]:
    """
    Find most recent .jpg file across all date folders.

    Args:
        clips_path: Path to clips directory
        stream_id: Stream identifier (e.g., 'harvard', 'nsw')

    Returns:
        URL path to thumbnail or None
    """
    clips_dir = Path(clips_path)
    if not clips_dir.exists():
        return None

    latest_jpg = None
    latest_time = 0
    latest_date = None
    latest_filename = None

    # Search all date folders
    for date_dir in clips_dir.iterdir():
        if not date_dir.is_dir():
            continue

        for file in date_dir.glob("*.jpg"):
            mtime = file.stat().st_mtime
            if mtime > latest_time:
                latest_time = mtime
                latest_date = date_dir.name
                latest_filename = file.name

    if latest_date and latest_filename:
        return f"/clips/{stream_id}/{latest_date}/{latest_filename}"

    return None


def get_today_visits(clips_path: str) -> int:
    """
    Count arrival clips for today.

    Args:
        clips_path: Path to clips directory

    Returns:
        Number of arrival clips today
    """
    today = datetime.now().strftime("%Y-%m-%d")
    clips = list_clips(clips_path, today)

    # Count clips with type 'arrival'
    return sum(1 for clip in clips if clip["type"] == "arrival")


def get_last_event(clips_path: str) -> Optional[dict]:
    """
    Get most recent event.

    Args:
        clips_path: Path to clips directory

    Returns:
        Dict with time, type, ago or None
    """
    clips_dir = Path(clips_path)
    if not clips_dir.exists():
        return None

    latest_file = None
    latest_time = 0
    latest_type = ""

    # Search recent date folders (last 7 days)
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        date_path = clips_dir / date

        if not date_path.exists():
            continue

        # Pattern: falcon_HHMMSS_type.ext
        pattern = re.compile(r'falcon_(\d{6})_(\w+)\.(\w+)$')

        for file in date_path.iterdir():
            match = pattern.match(file.name)
            if not match:
                continue

            mtime = file.stat().st_mtime
            if mtime > latest_time:
                latest_time = mtime
                latest_type = match.group(2)
                latest_file = file

    if not latest_file:
        return None

    # Calculate time ago
    now = datetime.now().timestamp()
    delta_seconds = int(now - latest_time)

    if delta_seconds < 60:
        ago = f"{delta_seconds}s ago"
    elif delta_seconds < 3600:
        ago = f"{delta_seconds // 60}m ago"
    elif delta_seconds < 86400:
        ago = f"{delta_seconds // 3600}h ago"
    else:
        ago = f"{delta_seconds // 86400}d ago"

    return {
        "time": datetime.fromtimestamp(latest_time).strftime("%H:%M:%S"),
        "type": latest_type,
        "ago": ago,
    }
