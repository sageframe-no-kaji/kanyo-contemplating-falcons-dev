"""
Configuration management for kanyo.
- Loads from config.yaml
- Loads secrets from .env file
- Environment variable overrides (KANYO_<KEY>)
- Sensible defaults with validation
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────────────────────────────────────
DEFAULTS: dict[str, Any] = {
    # Stream & Detection
    "video_source": "",  # YouTube URL (required)
    "detection_confidence": 0.5,  # 0.0–1.0
    "detection_interval": 60,  # seconds between notification checks
    "frame_interval": 30,  # process every Nth frame (30 = 2fps at 60fps)
    "model_path": "models/yolov8n.pt",  # YOLOv8 weights
    "detect_any_animal": True,  # treat any animal as falcon
    "exit_timeout": 300,  # 5 min - seconds before falcon "left" during visit
    "animal_classes": [14, 15, 16, 17, 18, 19, 20, 21, 22, 23],  # COCO animal IDs
    "timezone": "+00:00",  # GMT offset (e.g., -05:00 for NY, +10:00 for Sydney)
    # Output & Storage
    "output_dir": "output",  # results directory
    "data_dir": "data",  # thumbnails, events, etc.
    "events_file": "data/events.json",
    # Clip Extraction
    "clips_dir": "clips",
    "clip_arrival_before": 15,
    "clip_arrival_after": 30,
    "clip_departure_before": 30,
    "clip_departure_after": 15,
    "clip_merge_threshold": 180,
    "continuous_recording": False,
    "continuous_chunk_hours": 6,
    # Logging
    "log_level": "INFO",  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    "log_file": "logs/kanyo.log",
    # Notifications
    "telegram_enabled": False,
    "telegram_channel": "",  # Can be set in YAML or via TELEGRAM_CHANNEL env var
    "ntfy_enabled": False,
    "ntfy_topic": "",  # Can be set in YAML or via NTFY_ADMIN_TOPIC env var
    "ntfy_admin_enabled": False,  # Alias for ntfy_enabled
    "notification_cooldown_minutes": 5,
}

REQUIRED_FIELDS = ["video_source"]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _load_env_file(env_path: Path = Path(".env")) -> None:
    """Load .env file into os.environ (simple implementation without python-dotenv)."""
    if not env_path.exists():
        return

    with env_path.open() as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            # Parse KEY=VALUE
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Don't override existing env vars
                if key and key not in os.environ:
                    os.environ[key] = value


def _cast(value: str, reference: Any) -> Any:
    """Cast string value to match the type of reference."""
    if isinstance(reference, bool):
        return value.lower() in ("1", "true", "yes")
    if isinstance(reference, int):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(cfg: dict[str, Any]) -> None:
    """Override cfg values with KANYO_<KEY> env vars (in-place)."""
    for key, default in DEFAULTS.items():
        env_key = f"KANYO_{key.upper()}"
        if env_key in os.environ:
            value = os.environ[env_key]
            # Skip empty values
            if value:
                cfg[key] = _cast(value, default)


def _parse_timezone(tz_str: str) -> timezone:
    """Parse timezone string like '+10:00' or '-05:00' into timezone object."""
    if not tz_str or tz_str == "+00:00":
        return timezone.utc

    # Parse ±HH:MM format
    sign = 1 if tz_str[0] == "+" else -1
    parts = tz_str[1:].split(":")
    hours = int(parts[0])
    minutes = int(parts[1]) if len(parts) > 1 else 0
    offset = timedelta(hours=sign * hours, minutes=sign * minutes)
    return timezone(offset)


def get_now_tz(config: dict[str, Any]) -> datetime:
    """Get current time in the configured timezone."""
    tz_obj = config.get("timezone_obj", timezone.utc)
    return datetime.now(tz_obj)


def _validate(cfg: dict[str, Any]) -> None:
    """
    Validate configuration values.

    Raises ValueError if required fields are missing or timing constraints are violated.
    """
    # Required fields
    for field in REQUIRED_FIELDS:
        if not cfg.get(field):
            raise ValueError(f"Missing required config field: {field}")

    # Detection confidence range
    conf = cfg.get("detection_confidence", 0.5)
    if not 0.0 <= conf <= 1.0:
        raise ValueError("detection_confidence must be between 0.0 and 1.0")

    # Timing constraint validations
    exit_timeout = cfg.get("exit_timeout", 300)
    roosting_threshold = cfg.get("roosting_threshold", 1800)
    roosting_exit_timeout = cfg.get("roosting_exit_timeout", 600)
    activity_timeout = cfg.get("activity_timeout", 180)

    # activity_timeout must be less than roosting_exit_timeout
    # (otherwise activity state would immediately become departure)
    if activity_timeout >= roosting_exit_timeout:
        raise ValueError(
            f"activity_timeout ({activity_timeout}s) must be less than "
            f"roosting_exit_timeout ({roosting_exit_timeout}s). "
            f"Otherwise, activity periods can never be detected before departure."
        )

    # exit_timeout should be less than roosting_exit_timeout
    # (roosting should be more tolerant of absences than visiting)
    if exit_timeout >= roosting_exit_timeout:
        raise ValueError(
            f"exit_timeout ({exit_timeout}s) must be less than "
            f"roosting_exit_timeout ({roosting_exit_timeout}s). "
            f"Roosting state should tolerate longer absences than visiting state."
        )

    # roosting_threshold should be greater than exit_timeout
    # (otherwise falcon could depart before ever reaching roosting state)
    if roosting_threshold <= exit_timeout:
        raise ValueError(
            f"roosting_threshold ({roosting_threshold}s) must be greater than "
            f"exit_timeout ({exit_timeout}s). "
            f"Otherwise, falcon would always depart before reaching roosting state."
        )

    # Clip timing sanity checks
    clip_arrival_before = cfg.get("clip_arrival_before", 15)
    clip_arrival_after = cfg.get("clip_arrival_after", 30)
    clip_departure_before = cfg.get("clip_departure_before", 30)
    clip_departure_after = cfg.get("clip_departure_after", 15)

    if clip_arrival_before < 0 or clip_arrival_after < 0:
        raise ValueError(
            "clip_arrival_before and clip_arrival_after must be non-negative"
        )

    if clip_departure_before < 0 or clip_departure_after < 0:
        raise ValueError(
            "clip_departure_before and clip_departure_after must be non-negative"
        )

    # Warn if clip windows are very short
    min_clip_duration = 10  # seconds
    arrival_duration = clip_arrival_before + clip_arrival_after
    departure_duration = clip_departure_before + clip_departure_after

    if arrival_duration < min_clip_duration:
        logger.warning(
            f"Arrival clip duration ({arrival_duration}s) is very short. "
            f"Consider increasing clip_arrival_before or clip_arrival_after."
        )

    if departure_duration < min_clip_duration:
        logger.warning(
            f"Departure clip duration ({departure_duration}s) is very short. "
            f"Consider increasing clip_departure_before or clip_departure_after."
        )

    # short_visit_threshold should be reasonable
    short_visit_threshold = cfg.get("short_visit_threshold", 600)
    if short_visit_threshold < 60:
        raise ValueError(
            f"short_visit_threshold ({short_visit_threshold}s) is too short. "
            f"Minimum recommended value is 60 seconds."
        )

    # frame_interval sanity
    frame_interval = cfg.get("frame_interval", 3)
    if frame_interval < 1:
        raise ValueError("frame_interval must be at least 1")
    if frame_interval > 60:
        logger.warning(
            f"frame_interval ({frame_interval}) is very high. "
            f"Detection will be coarse (< 1 detection per second at 30fps)."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """
    Load configuration with priority: env vars > YAML file > defaults.
    Also loads .env file for secrets/credentials.
    """
    # Load .env file first
    _load_env_file()

    cfg = DEFAULTS.copy()

    # Load YAML if present
    config_path = Path(path)
    if config_path.exists():
        with config_path.open() as f:
            file_cfg = yaml.safe_load(f) or {}
        cfg.update(file_cfg)

    _apply_env_overrides(cfg)
    _validate(cfg)

    # Parse timezone string into timezone object
    if "timezone" in cfg and isinstance(cfg["timezone"], str):
        cfg["timezone_obj"] = _parse_timezone(cfg["timezone"])
    else:
        cfg["timezone_obj"] = timezone.utc

    return cfg
