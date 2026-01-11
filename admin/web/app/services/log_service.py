"""
Log service for reading kanyo.log files from disk.

Replaces docker logs with persistent file-based logging.
"""

import subprocess
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
        since: "startup", "1h", "8h", "24h", "3d", "7d"
        lines: Max lines to return
        levels: List of log levels to include (e.g., ["INFO", "ERROR"])
        show_context: If True, include DEBUG lines within ±5 lines of EVENT logs

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
    elif since == "8h":
        cutoff = now_utc - timedelta(hours=8)
    elif since == "24h":
        cutoff = now_utc - timedelta(hours=24)
    elif since == "3d":
        cutoff = now_utc - timedelta(days=3)
    elif since == "7d":
        cutoff = now_utc - timedelta(days=7)
    elif since == "startup":
        # Find last startup message
        cutoff = _find_last_startup(log_path)

    # Use tail for efficiency - read last 5000 lines
    raw_lines = _tail_file(log_path, lines=5000)

    # Show context only allowed for short time ranges (up to 24h)
    # For longer ranges, too much data to process efficiently
    context_allowed = since in ("startup", "1h", "8h", "24h")
    effective_show_context = show_context and context_allowed

    # Determine if we need to parse DEBUG lines
    parse_debug = effective_show_context or (levels and "DEBUG" in levels)

    # Parse and filter by time (skip DEBUG unless needed for massive speedup)
    all_lines = []
    for line in raw_lines:
        # Quick check: skip DEBUG lines unless we need them
        if not parse_debug and " | DEBUG " in line:
            continue

        parsed = _parse_log_line(line)
        if parsed:
            # Filter by cutoff time
            if cutoff is not None and parsed["timestamp"] < cutoff:
                continue
            all_lines.append(parsed)

    # Apply level filtering and optional smart DEBUG context
    if effective_show_context:
        # Smart filtering: show ALL non-DEBUG levels + DEBUG only near EVENTs
        # This provides full context around events regardless of levels filter
        all_lines = _add_debug_context(all_lines, context_lines=5, levels=None)
    elif levels:
        # Normal filtering: only requested levels
        all_lines = [log for log in all_lines if log["level"] in levels]

    # Return last N lines
    return all_lines[-lines:]


def _tail_file(path: Path, lines: int = 5000) -> list[str]:
    """Read last N lines efficiently using tail."""
    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.splitlines()
    except Exception:
        # Fallback to Python if tail fails
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.readlines()[-lines:]


def _add_debug_context(
    all_lines: list[dict], context_lines: int = 5, levels: list[str] | None = None
) -> list[dict]:
    """
    Smart DEBUG filtering: keep requested non-DEBUG logs, include DEBUG only near EVENTs.

    This matches event-search.sh behavior: ERROR/INFO/WARNING/EVENT shown if requested,
    DEBUG only within ±N lines of an EVENT.

    Args:
        all_lines: All parsed log lines
        context_lines: Number of lines before/after EVENT to include DEBUG
        levels: Requested log levels (if None, include all non-DEBUG levels)
    """
    # Find indices where DEBUG should be included (±N lines around EVENTs)
    debug_allowed_indices = set()
    for i, line in enumerate(all_lines):
        if line["level"] == "EVENT":
            for j in range(
                max(0, i - context_lines),
                min(len(all_lines), i + context_lines + 1),
            ):
                debug_allowed_indices.add(j)

    # Filter based on levels and DEBUG context rules
    result = []
    for i, line in enumerate(all_lines):
        level = line["level"]

        if level == "DEBUG":
            # Include DEBUG only if within context window
            if i in debug_allowed_indices:
                result.append(line)
        else:
            # Include non-DEBUG if in requested levels (or if no levels specified)
            if levels is None or level in levels:
                result.append(line)

    return result


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
