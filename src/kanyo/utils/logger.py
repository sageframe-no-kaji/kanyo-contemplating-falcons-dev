"""
Logging utility for kanyo.

Behavior:
- Reads log_level and log_file from config (via setup_logging_from_config)
- Logs to BOTH console (stderr) and file (logs/kanyo.log by default)
- Format: "2025-12-15 10:30:00 | INFO | module_name | message"
- Call setup_logging() once at startup, then get_logger(__name__) in each module
- Log levels: DEBUG, INFO, EVENT, WARNING, ERROR, CRITICAL

Custom EVENT level (25) for falcon events:
- DEBUG (10): Heartbeats, raw detections, state checks
- INFO (20): Startup, config, connections
- EVENT (25): Falcon arrivals, departures, clips, notifications
- WARNING (30): Unusual but not errors (stream hiccups)
- ERROR (40): Actual errors
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Custom EVENT level (between INFO and WARNING)
# ──────────────────────────────────────────────────────────────────────────────
EVENT = 25
logging.addLevelName(EVENT, "EVENT")


def _event(self, message, *args, **kwargs):
    """Log a message at the EVENT level."""
    if self.isEnabledFor(EVENT):
        self.log(EVENT, message, *args, **kwargs)


# Add event() method to Logger class
logging.Logger.event = _event

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LEVEL = "INFO"
DEFAULT_LOG_FILE = "logs/kanyo.log"

_initialized = False


# ──────────────────────────────────────────────────────────────────────────────
# Timezone-aware formatter
# ──────────────────────────────────────────────────────────────────────────────
class TimezoneFormatter(logging.Formatter):
    """Formatter that uses a configurable timezone offset."""

    def __init__(self, fmt=None, datefmt=None, tz_offset_hours: float = 0):
        super().__init__(fmt, datefmt)
        self.tz = timezone(timedelta(hours=tz_offset_hours))

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=self.tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
def setup_logging(
    level: str = DEFAULT_LEVEL, log_file: str = DEFAULT_LOG_FILE, tz_offset_hours: float = 0
) -> None:
    """Initialize root logger with console + file handlers. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = TimezoneFormatter(LOG_FORMAT, DATE_FORMAT, tz_offset_hours=tz_offset_hours)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(console)
    root.addHandler(file_handler)

    _initialized = True


def setup_logging_from_config(config: dict[str, Any]) -> None:
    """Initialize logging using values from a loaded config dict."""
    tz_str = config.get("timezone", "+00:00")
    # Parse timezone string like "-05:00" or "+10:00" to hours
    sign = -1 if tz_str.startswith("-") else 1
    parts = tz_str.replace("-", "").replace("+", "").split(":")
    tz_hours = sign * (int(parts[0]) + int(parts[1]) / 60)

    setup_logging(
        level=config.get("log_level", DEFAULT_LEVEL),
        log_file=config.get("log_file", DEFAULT_LOG_FILE),
        tz_offset_hours=tz_hours,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name. Calls setup_logging() if needed."""
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)
