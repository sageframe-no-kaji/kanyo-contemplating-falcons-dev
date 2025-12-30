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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Custom EVENT level (between INFO and WARNING)
# ──────────────────────────────────────────────────────────────────────────────
EVENT = 25
logging.addLevelName(EVENT, "EVENT")


def _event(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
    """Log a message at the EVENT level."""
    if self.isEnabledFor(EVENT):
        self.log(EVENT, message, *args, **kwargs)


# Add event() method to Logger class
logging.Logger.event = _event

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s UTC | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LEVEL = "INFO"
DEFAULT_LOG_FILE = "logs/kanyo.log"

_initialized = False


# ──────────────────────────────────────────────────────────────────────────────
# UTC-only formatter
# ──────────────────────────────────────────────────────────────────────────────
class UTCFormatter(logging.Formatter):
    """Formatter that always uses UTC for log timestamps."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
def setup_logging(level: str = DEFAULT_LEVEL, log_file: str = DEFAULT_LOG_FILE) -> None:
    """Initialize root logger with console + file handlers (UTC timestamps). Safe to call multiple times."""
    global _initialized
    if _initialized:
        return

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = UTCFormatter(LOG_FORMAT, DATE_FORMAT)

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
    """Initialize logging using values from a loaded config dict. Always uses UTC for timestamps."""
    setup_logging(
        level=config.get("log_level", DEFAULT_LEVEL),
        log_file=config.get("log_file", DEFAULT_LOG_FILE),
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name. Calls setup_logging() if needed."""
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)
