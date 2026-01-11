"""
Log service for reading kanyo.log files from disk.

Replaces docker logs with persistent file-based logging.
Supports rotated log files (kanyo.log, kanyo.log.2026-01-10, etc.)
"""

import glob
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _get_log_files(stream_id: str, since: str) -> list[Path]:
    """
    Get all relevant log files for the time range, oldest first.

    Rotated files are named: kanyo.log.YYYY-MM-DD
    """
    log_dir = Path(f"/data/{stream_id}/logs")
    main_log = log_dir / "kanyo.log"

    if not log_dir.exists():
        return []

    # For short ranges, only need current file
    if since in ("startup", "1h", "8h"):
        return [main_log] if main_log.exists() else []

    # For 24h+, include rotated files
    now_utc = datetime.now(timezone.utc)
    if since == "24h":
        cutoff_date = (now_utc - timedelta(days=1)).date()
    elif since == "3d":
        cutoff_date = (now_utc - timedelta(days=3)).date()
    elif since == "7d":
        cutoff_date = (now_utc - timedelta(days=7)).date()
    else:
        cutoff_date = None

    # Find rotated files matching pattern kanyo.log.YYYY-MM-DD
    rotated_pattern = str(log_dir / "kanyo.log.[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]")
    rotated_files = sorted(glob.glob(rotated_pattern))

    # Filter by date and build list (oldest first)
    result = []
    for f in rotated_files:
        try:
            date_str = Path(f).name.replace("kanyo.log.", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if cutoff_date is None or file_date >= cutoff_date:
                result.append(Path(f))
        except ValueError:
            continue

    # Add current log last (most recent)
    if main_log.exists():
        result.append(main_log)

    return result


def get_logs(
    stream_id: str,
    since: str = "startup",
    lines: int = 500,
    levels: list[str] | None = None,
    show_context: bool = False,
) -> list[dict]:
    """
    Read logs from kanyo.log files (including rotated files for longer ranges).

    Args:
        stream_id: Stream identifier
        since: "startup", "1h", "8h", "24h", "3d", "7d"
        lines: Max lines to return
        levels: List of log levels to include (e.g., ["INFO", "ERROR"])
        show_context: If True, include DEBUG lines within ±5 lines of EVENT logs

    Returns:
        List of log line dicts with timestamp, level, module, message
    """
    # Get all relevant log files
    log_files = _get_log_files(stream_id, since)

    if not log_files:
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
        # Find last startup message in most recent file
        main_log = log_files[-1] if log_files else None
        cutoff = _find_last_startup(main_log) if main_log else None

    # Read from all relevant log files
    raw_lines = []
    for log_file in log_files:
        if log_file.name == "kanyo.log":
            # Use tail for current file (most efficient)
            raw_lines.extend(_tail_file(log_file, lines=5000))
        else:
            # Read rotated files entirely (already filtered by date)
            raw_lines.extend(_read_file(log_file))

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


def _read_file(path: Path) -> list[str]:
    """Read entire file (for rotated log files)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return [line.rstrip("\n") for line in f]
    except Exception:
        return []


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
