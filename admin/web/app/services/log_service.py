"""
Log service for reading kanyo.log files from disk.

Replaces docker logs with persistent file-based logging.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_logs(
    stream_id: str,
    since: str = "startup",
    lines: int = 500,
    levels: list[str] | None = None,
    show_context: bool = False,
) -> list[dict]:
    """
    Read logs from kanyo.log file.

    Args:
        stream_id: Stream identifier
        since: "startup", "1h", "24h", "7d", "all"
        lines: Max lines to return
        levels: List of log levels to include (e.g., ["INFO", "ERROR"])
        show_context: If True, include 3 DEBUG lines before/after EVENT logs

    Returns:
        List of log line dicts with timestamp, level, module, message
    """
    log_path = Path(f"/data/{stream_id}/logs/kanyo.log")

    if not log_path.exists():
        return []

    # Calculate cutoff time (all times are UTC since logs are in UTC)
    cutoff = None
    now_utc = datetime.now(timezone.utc)

    if since == "1h":
        cutoff = now_utc - timedelta(hours=1)
    elif since == "24h":
        cutoff = now_utc - timedelta(hours=24)
    elif since == "7d":
        cutoff = now_utc - timedelta(days=7)
    elif since == "startup":
        # Find last startup message
        cutoff = _find_last_startup(log_path)
    # "all" = no cutoff

    # Read and filter
    all_lines = []
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parsed = _parse_log_line(line)
            if parsed:
                # Filter by cutoff time
                if cutoff is not None and parsed["timestamp"] < cutoff:
                    continue
                all_lines.append(parsed)

    # If show_context is enabled, find EVENT logs and include surrounding DEBUG lines
    if show_context and levels and "EVENT" in levels:
        result_lines = []
        context_window = 3

        for i, log in enumerate(all_lines):
            # Always include lines matching the requested levels
            if log["level"] in levels:
                result_lines.append(log)

                # If this is an EVENT, add context before and after
                if log["level"] == "EVENT":
                    # Add 3 DEBUG lines before
                    for j in range(max(0, i - context_window), i):
                        prev_log = all_lines[j]
                        if prev_log["level"] == "DEBUG" and prev_log not in result_lines:
                            result_lines.append(prev_log)

                    # Add 3 DEBUG lines after
                    for j in range(i + 1, min(len(all_lines), i + 1 + context_window)):
                        next_log = all_lines[j]
                        if next_log["level"] == "DEBUG" and next_log not in result_lines:
                            result_lines.append(next_log)

        # Sort by timestamp to maintain chronological order
        result_lines.sort(key=lambda x: x["timestamp"])
        log_lines = result_lines
    else:
        # Normal filtering without context
        log_lines = []
        for log in all_lines:
            if levels and log["level"] not in levels:
                continue
            log_lines.append(log)

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

    Returns UTC-aware datetime for accurate time comparisons.
    """
    try:
        # Split on " | " to separate components
        parts = line.split(" | ", 3)
        if len(parts) >= 4:
            # Parse timestamp (remove "UTC" suffix if present)
            timestamp_str = parts[0].strip().replace(" UTC", "")
            # Parse as naive datetime then make UTC-aware
            timestamp_naive = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            timestamp = timestamp_naive.replace(tzinfo=timezone.utc)

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
