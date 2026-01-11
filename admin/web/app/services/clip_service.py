"""Clip browsing and management service."""

from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
import re
from PIL import Image, ImageDraw


def get_stream_timezone(stream_timezone: str) -> ZoneInfo:
    """
    Parse stream timezone string to ZoneInfo object.

    Args:
        stream_timezone: IANA timezone name (e.g., "Australia/Sydney") or offset (e.g., "+11:00")

    Returns:
        ZoneInfo object for the stream's timezone
    """
    # Handle IANA timezone names
    if "/" in stream_timezone or stream_timezone in ("UTC", "GMT"):
        try:
            return ZoneInfo(stream_timezone)
        except Exception:
            return ZoneInfo("UTC")

    # Handle offset format (legacy) - just use UTC for admin
    # The detection system will handle proper offset parsing
    return ZoneInfo("UTC")


def get_stream_today(stream_timezone: str) -> str:
    """
    Get today's date in the stream's timezone.

    Args:
        stream_timezone: IANA timezone name (e.g., "Australia/Sydney")

    Returns:
        Date string in YYYY-MM-DD format for stream's local "today"
    """
    tz = get_stream_timezone(stream_timezone)
    return datetime.now(tz).strftime("%Y-%m-%d")


def get_stream_date_offset(stream_timezone: str, offset_days: int) -> str:
    """
    Get date relative to stream's local timezone.

    Args:
        stream_timezone: IANA timezone name
        offset_days: Days offset (0=today, 1=yesterday, -1=tomorrow)

    Returns:
        Date string in YYYY-MM-DD format
    """
    tz = get_stream_timezone(stream_timezone)
    target_date = datetime.now(tz) - timedelta(days=offset_days)
    return target_date.strftime("%Y-%m-%d")


def create_visit_thumbnail(arrival_path: Path, departure_path: Path, output_path: Path) -> bool:
    """
    Create composite thumbnail for visit clip combining arrival and departure.
    Split diagonally from lower-left to upper-right.

    Args:
        arrival_path: Path to arrival thumbnail
        departure_path: Path to departure thumbnail
        output_path: Path to save composite thumbnail

    Returns:
        True if successful, False otherwise
    """
    try:
        # Load images
        arrival = Image.open(arrival_path)
        departure = Image.open(departure_path)

        # Get dimensions
        width, height = arrival.size

        # Create composite starting with arrival
        composite = arrival.copy()

        # Create mask for diagonal split (lower-left to upper-right)
        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)

        # Fill lower-right triangle (for departure)
        draw.polygon([(0, height), (width, height), (width, 0)], fill=255)

        # Paste departure using mask
        composite.paste(departure, (0, 0), mask)

        # Draw diagonal line from lower-left to upper-right
        draw = ImageDraw.Draw(composite)
        draw.line([(0, height), (width, 0)], fill="white", width=4)

        # Save composite
        composite.save(output_path, "JPEG", quality=85)
        return True

    except Exception as e:
        print(f"Error creating visit thumbnail: {e}")
        return False


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
    pattern = re.compile(r"falcon_(\d{6})_(\w+)\.(\w+)$")

    for clip_file in sorted(date_path.iterdir(), reverse=True):
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
        is_video = ext.lower() in ["mp4", "avi", "mov", "mkv"]

        # Check if thumbnail exists for videos
        has_thumbnail = True
        if is_video:
            thumb_path = clip_file.with_suffix(".jpg")

            # For visit clips, create composite thumbnail from arrival + nearest departure
            if clip_type == "visit" and not thumb_path.exists():
                arrival_thumb = date_path / f"falcon_{time_str}_arrival.jpg"

                # Find departure thumbnail - it may be at a later time
                departure_thumb = None
                for dep_file in sorted(date_path.glob("falcon_*_departure.jpg")):
                    dep_time_str = dep_file.name.split("_")[1]
                    if dep_time_str >= time_str:  # Find first departure >= arrival time
                        departure_thumb = dep_file
                        break

                if arrival_thumb.exists() and departure_thumb and departure_thumb.exists():
                    create_visit_thumbnail(arrival_thumb, departure_thumb, thumb_path)

            has_thumbnail = thumb_path.exists()

        clips.append(
            {
                "filename": clip_file.name,
                "time": time,
                "type": clip_type,
                "is_video": is_video,
                "has_thumbnail": has_thumbnail,
                "size_bytes": clip_file.stat().st_size,
            }
        )

    return clips


def list_clips_since(clips_path: str, stream_timezone: str, hours: int = 24) -> list[dict]:
    """
    List clips from the last N hours (relative time, not calendar days).

    Args:
        clips_path: Path to clips directory
        stream_timezone: Stream's IANA timezone
        hours: Number of hours to look back

    Returns:
        List of clip dictionaries sorted by time (newest first)
    """
    tz = get_stream_timezone(stream_timezone)
    now = datetime.now(tz)
    cutoff = now - timedelta(hours=hours)

    clips = []
    clips_dir = Path(clips_path)

    if not clips_dir.exists():
        return []

    # Get date folders that might contain relevant clips
    # Need to check today and potentially several previous days
    dates_to_check = set()
    current = cutoff
    while current <= now:
        dates_to_check.add(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    # Pattern: falcon_HHMMSS_type.ext
    pattern = re.compile(r"falcon_(\d{6})_(\w+)\.(\w+)$")

    for date_str in sorted(dates_to_check):
        date_path = clips_dir / date_str
        if not date_path.exists():
            continue

        for clip_file in date_path.iterdir():
            if not clip_file.is_file():
                continue

            match = pattern.match(clip_file.name)
            if not match:
                continue

            time_str, clip_type, ext = match.groups()

            # Parse clip datetime
            hour = int(time_str[:2])
            minute = int(time_str[2:4])
            second = int(time_str[4:6])

            try:
                clip_date = datetime.strptime(date_str, "%Y-%m-%d")
                clip_dt = (
                    tz.localize(clip_date.replace(hour=hour, minute=minute, second=second))
                    if hasattr(tz, "localize")
                    else clip_date.replace(hour=hour, minute=minute, second=second, tzinfo=tz)
                )
            except Exception:
                continue

            # Filter by cutoff time
            if clip_dt < cutoff:
                continue

            time_display = f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
            is_video = ext.lower() in ["mp4", "avi", "mov", "mkv"]

            # Check thumbnail
            has_thumbnail = True
            if is_video:
                thumb_path = clip_file.with_suffix(".jpg")

                # For visit clips, create composite thumbnail
                if clip_type == "visit" and not thumb_path.exists():
                    arrival_thumb = date_path / f"falcon_{time_str}_arrival.jpg"
                    departure_thumb = None
                    for dep_file in sorted(date_path.glob("falcon_*_departure.jpg")):
                        dep_time_str = dep_file.name.split("_")[1]
                        if dep_time_str >= time_str:
                            departure_thumb = dep_file
                            break
                    if arrival_thumb.exists() and departure_thumb and departure_thumb.exists():
                        create_visit_thumbnail(arrival_thumb, departure_thumb, thumb_path)

                has_thumbnail = thumb_path.exists()

            clips.append(
                {
                    "filename": clip_file.name,
                    "date": date_str,
                    "time": time_display,
                    "datetime": clip_dt,
                    "type": clip_type,
                    "is_video": is_video,
                    "has_thumbnail": has_thumbnail,
                    "size_bytes": clip_file.stat().st_size,
                }
            )

    # Sort by datetime, newest first
    clips.sort(key=lambda x: x["datetime"], reverse=True)
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
        pattern = re.compile(r"falcon_(\d{6})_(\w+)\.(\w+)$")

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


def get_today_events(clips_path: str) -> list[dict]:
    """Get today's events from clips, deduplicated by timestamp.

    Args:
        clips_path: Path to clips directory

    Returns:
        List of events with time, type, and filename
    """
    today = datetime.now().strftime("%Y-%m-%d")
    clips = list_clips(clips_path, today)

    # Group by time, prefer showing arrival/departure over visit
    events_by_time = {}
    for clip in clips:
        time = clip["time"]
        clip_type = clip["type"]

        # Skip if we already have this time with a better type
        if time in events_by_time:
            existing = events_by_time[time]
            # Prefer arrival/departure over visit
            if existing["type"] in ["arrival", "departure"]:
                continue

        events_by_time[time] = {
            "time": time,
            "type": clip_type,
            "filename": clip["filename"],
        }

    # Sort by time (most recent first)
    return sorted(events_by_time.values(), key=lambda x: x["time"], reverse=True)
