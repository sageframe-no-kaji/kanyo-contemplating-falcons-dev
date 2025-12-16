"""
Configuration management for kanyo.
- Loads from config.yaml
- Environment variable overrides (KANYO_<KEY>)
- Sensible defaults with validation
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# ──────────────────────────────────────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────────────────────────────────────
DEFAULTS: dict[str, Any] = {
    "video_source": "",  # YouTube URL (required)
    "detection_confidence": 0.5,  # 0.0–1.0
    "detection_interval": 60,  # seconds between checks
    "output_dir": "output",  # results directory
    "model_path": "models/yolov8n.pt",  # YOLOv8 weights
}

REQUIRED_FIELDS = ["video_source"]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
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
            cfg[key] = _cast(os.environ[env_key], default)


def _validate(cfg: dict[str, Any]) -> None:
    """Raise ValueError if required fields are missing or invalid."""
    for field in REQUIRED_FIELDS:
        if not cfg.get(field):
            raise ValueError(f"Missing required config field: {field}")

    conf = cfg.get("detection_confidence", 0.5)
    if not 0.0 <= conf <= 1.0:
        raise ValueError("detection_confidence must be between 0.0 and 1.0")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """
    Load configuration with priority: env vars > YAML file > defaults.
    """
    cfg = DEFAULTS.copy()

    # Load YAML if present
    config_path = Path(path)
    if config_path.exists():
        with config_path.open() as f:
            file_cfg = yaml.safe_load(f) or {}
        cfg.update(file_cfg)

    _apply_env_overrides(cfg)
    _validate(cfg)
    return cfg
