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

Smart DEBUG buffering:
- DEBUG logs are kept in a ring buffer (not written to file)
- When EVENT/WARNING/ERROR occurs, buffer is flushed and DEBUG is captured for 5s
- This keeps DEBUG context around events while eliminating spam
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from collections import deque
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

# Smart DEBUG buffering settings
DEBUG_BUFFER_SIZE = 150  # Keep last N DEBUG logs in memory
DEBUG_CAPTURE_WINDOW = 5.0  # Seconds to capture DEBUG after an event

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
# Smart buffered file handler - keeps DEBUG around events only + daily rotation
# ──────────────────────────────────────────────────────────────────────────────
class BufferedDebugHandler(logging.handlers.TimedRotatingFileHandler):
    """
    File handler with smart DEBUG buffering and daily rotation.

    - DEBUG logs are kept in a ring buffer (not written to file)
    - When EVENT/WARNING/ERROR occurs:
      1. Flush the DEBUG buffer to file (context before event)
      2. Enable DEBUG pass-through for 5 seconds (context after event)
    - INFO logs always written immediately
    - Rotates at midnight UTC, keeps 7 days of logs
    - Rotated files: kanyo.log.2026-01-10, kanyo.log.2026-01-09, etc.
    """

    def __init__(
        self,
        filename,
        buffer_size=DEBUG_BUFFER_SIZE,
        capture_window=DEBUG_CAPTURE_WINDOW,
        backup_count=7,
        **kwargs,
    ):
        # Extract encoding for TimedRotatingFileHandler
        encoding = kwargs.pop("encoding", "utf-8")

        # Initialize timed rotating handler - rotates at midnight UTC
        super().__init__(
            filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding=encoding,
            utc=True,
        )

        self._debug_buffer: deque = deque(maxlen=buffer_size)
        self._capture_window = capture_window
        self._capture_until = 0.0  # Timestamp when DEBUG pass-through expires

    def emit(self, record):
        """Handle a log record with smart DEBUG buffering."""
        try:
            if record.levelno == logging.DEBUG:
                # Check if we're in the capture window
                if time.monotonic() < self._capture_until:
                    # Write DEBUG directly during capture window
                    super().emit(record)
                else:
                    # Buffer DEBUG log (formatted, ready to write)
                    msg = self.format(record)
                    self._debug_buffer.append(msg)
            elif record.levelno >= EVENT:  # EVENT (25), WARNING, ERROR, CRITICAL
                # Flush DEBUG buffer first (context before event)
                self._flush_debug_buffer()
                # Write the event
                super().emit(record)
                # Start capture window for DEBUG logs after this event
                self._capture_until = time.monotonic() + self._capture_window
            else:
                # INFO level - write directly
                super().emit(record)
        except Exception:
            self.handleError(record)

    def _flush_debug_buffer(self):
        """Write all buffered DEBUG logs to file."""
        while self._debug_buffer:
            msg = self._debug_buffer.popleft()
            # Write raw formatted message (already includes newline from format)
            self.stream.write(msg + self.terminator)
        self.flush()


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
def setup_logging(level: str = DEFAULT_LEVEL, log_file: str = DEFAULT_LOG_FILE) -> None:
    """Initialize root logger with console + file handlers (UTC timestamps).

    Uses BufferedDebugHandler for file output to reduce DEBUG spam while
    preserving context around events.

    Safe to call multiple times.
    """
    global _initialized

    root = logging.getLogger()

    # ALWAYS update level (even if handlers already exist)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if _initialized:
        return  # Don't add duplicate handlers, but level is already updated above

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = UTCFormatter(LOG_FORMAT, DATE_FORMAT)

    # Console: show all log levels (including DEBUG if enabled)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)

    # File: use smart buffered handler for DEBUG logs
    file_handler = BufferedDebugHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

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
