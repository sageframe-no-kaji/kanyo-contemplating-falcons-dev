"""
Logging utility for kanyo.

Behavior:
- Reads log_level and log_file from config.yaml (optional keys, sensible defaults)
- Logs to BOTH console (stderr) and file (logs/kanyo.log by default)
- Format: "2025-12-15 10:30:00 | INFO | module_name | message"
- Call setup_logging() once at startup, then get_logger(__name__) in each module
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LEVEL = "INFO"
DEFAULT_LOG_FILE = "logs/kanyo.log"

_initialized = False


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
def setup_logging(level: str = DEFAULT_LEVEL, log_file: str = DEFAULT_LOG_FILE) -> None:
    """Initialize root logger with console + file handlers. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(console)
    root.addHandler(file_handler)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name. Calls setup_logging() if needed."""
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)
