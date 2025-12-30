"""
Log service for reading kanyo.log files from disk.

Replaces docker logs with persistent file-based logging.
"""

from datetime import datetime, timedelta
from pathlib import Path


def get_logs(
    stream_id: str,
    since: str = "startup",
    lines: int = 500,
    levels: list[str] | None = None,
) -> list[dict]:
    """
    Read logs from kanyo.log file.

    Args:
        stream_id: Stream identifier
        since: "startup", "1h", "24h", "7d", "all"
        lines: Max lines to return
        levels: List of log levels to include (e.g., ["INFO", "ERROR"])

    Returns:
        List of log line dicts with timestamp, level, module, message
    """
    log_path = Path(f"/data/{stream_id}/logs/kanyo.log")

    if not log_path.exists():
        return []

    # Calculate cutoff time
    cutoff = None
    if since == "1h":
        cutoff = datetime.now() - timedelta(hours=1)
    elif since == "24h":
        cutoff = datetime.now() - timedelta(hours=24)
    elif since == "7d":
        cutoff = datetime.now() - timedelta(days=7)
    elif since == "startup":
        # Find last startup message
        cutoff = _find_last_startup(log_path)
    # "all" = no cutoff

    # Read and filter
    log_lines = []
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parsed = _parse_log_line(line)
            if parsed:
                # Filter by cutoff time
                if cutoff is not None and parsed["timestamp"] < cutoff:
                    continue

                # Filter by log levels
                if levels and parsed["level"] not in levels:
                    continue

                log_lines.append(parsed)

    # Return last N lines
    return log_lines[-lines:]


def _find_last_startup(log_path: Path) -> datetime:
    """Find timestamp of last 'BUFFER-BASED FALCON MONITOR' or similar startup line."""
    last_startup = None
    startup_markers = [
        "BUFFER-BASED FALCON MONITOR",
        "Starting Buffer-Based Falcon Monitoring",
        "KANYO STARTING",
    ]

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if any(marker in line for marker in startup_markers):
                parsed = _parse_log_line(line)
                if parsed:
                    last_startup = parsed["timestamp"]

    return last_startup or datetime.min


def _parse_log_line(line: str) -> dict | None:
    """
    Parse log line into structured dict.

    Expected format: 2025-12-30 12:08:03 UTC | INFO     | module | message
    """
    try:
        # Split on " | " to separate components
        parts = line.split(" | ", 3)
        if len(parts) >= 4:
            # Parse timestamp (remove "UTC" suffix if present)
            timestamp_str = parts[0].strip().replace(" UTC", "")
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

            return {
                "timestamp": timestamp,
                "level": parts[1].strip(),
                "module": parts[2].strip(),
                "message": parts[3].strip(),
                "raw": line.strip(),
            }
    except (ValueError, IndexError):
        pass

    return None
