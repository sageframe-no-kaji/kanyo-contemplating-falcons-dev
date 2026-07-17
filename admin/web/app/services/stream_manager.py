"""Stream creation: writes the on-disk stream definition (issue #6).

create_stream() creates the stream's data directory under DATA_PATH using the
parent-mount layout from issue #5 (/data/kanyo-<id>/, i.e. host
/opt/services/kanyo-<id>/), writes a config.yaml built from the canonical
template defaults, and creates the clips/ and logs/ dirs.

Deliberately bounded: it does NOT create containers, edit compose files, or
touch the host beyond the stream directory. Starting a detector is a manual
paste into the host compose following the template's profile pattern (see
docker/docker-compose.yml, bigbear example) — dynamic orchestration is
issue #7. The success UI surfaces those next steps.

The admin container cannot import src/kanyo or read configs/ (its build
context is admin/web only), so both the template defaults and the validation
rules are admin-local mirrors, pinned by tests in the main test suite:

# MIRROR — TEMPLATE_DEFAULTS must equal configs/config.template.yaml
# (tests/test_admin_stream_manager.py loads the real template and compares).
# MIRROR — validation mirrors the relevant subset of
# src/kanyo/utils/config._validate (same test module pins the constraints).
"""

import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import yaml
from app.config import settings

# Stream ids that collide with service dirs or routes.
RESERVED_STREAM_IDS = {"admin", "nvidia", "code", "viewer", "new"}

# ──────────────────────────────────────────────────────────────────────────────
# Template defaults
# ──────────────────────────────────────────────────────────────────────────────
# MIRROR — keep byte-in-sync with configs/config.template.yaml. The mirror
# test parses the real template and asserts equality, so drift fails CI.
TEMPLATE_DEFAULTS: dict[str, Any] = {
    "stream_name": "My Falcon Cam",
    "video_source": "https://youtube.com/watch?v=YOUR_STREAM_ID",
    "creature_name": "falcon",
    "creature_emoji": "🦅",
    "detection_confidence": 0.3,
    "detection_confidence_ir": 0.25,
    "frame_interval": 3,
    "model_path": "models/yolov8n.pt",
    "detect_any_animal": True,
    "detection_summary_interval": 300,
    "timezone": "UTC",
    "exit_timeout": 90,
    "roosting_threshold": 1800,
    "presence_enabled": True,
    "presence_sustain_confidence": 0.15,
    "presence_region_margin_frac": 0.25,
    "presence_motion_pixel_threshold": 25,
    "presence_motion_min_area_frac": 0.02,
    "presence_global_change_frac": 0.5,
    "presence_absence_failsafe_seconds": 3600,
    "bird_count_enabled": False,
    "bird_count_confirmation_seconds": 10,
    "significance_filter_enabled": True,
    "merge_window_seconds": 300,
    "min_significant_seconds": 30,
    "damping_arrivals_threshold": 8,
    "damping_window_hours": 1,
    "roosting_recording_mode": "continuous",
    "roosting_detection_interval": 30,
    "stream_recovery_threshold": 30,
    "stream_recovery_confirmation": 10,
    "stream_read_timeout_s": 10.0,
    "arrival_confirmation_seconds": 10,
    "arrival_confirmation_ratio": 0.3,
    "notify_on_startup": False,
    "record_arrival_on_startup": False,
    "clips_dir": "clips",
    "clip_arrival_before": 15,
    "clip_arrival_after": 30,
    "clip_departure_before": 30,
    "clip_departure_after": 15,
    "telegram_enabled": False,
    "telegram_channel": "",
    "notification_cooldown_minutes": 5,
    "ntfy_admin_enabled": False,
    "ntfy_topic": "kanyo_admin_errors",
    "display": {
        "short_name": "My Cam",
        "location": "City, State/Country",
        "coordinates": [0.0, 0.0],
        "species": "Peregrine Falcon (Falco peregrinus)",
        "nest_status": "Active nesting site",
        "maintainer": "Organization Name",
        "maintainer_url": "https://example.com",
        "description": ("Brief description of the camera location and purpose for public viewers."),
    },
    "log_level": "DEBUG",
    "log_file": "logs/kanyo.log",
}


def stream_dir_for(stream_id: str) -> Path:
    """New streams use the parent-mount layout: /data/kanyo-<id> (issue #5)."""
    return settings.DATA_PATH / f"kanyo-{stream_id}"


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────
def validate_stream_id(stream_id: str) -> tuple[bool, str]:
    """Validate stream ID format."""
    if not stream_id:
        return False, "Stream ID is required"
    if not re.match(r"^[a-z][a-z0-9_-]*$", stream_id):
        return False, "Stream ID must start with lowercase letter, contain only a-z, 0-9, _, -"
    if len(stream_id) > 32:
        return False, "Stream ID must be 32 characters or less"
    if stream_id in RESERVED_STREAM_IDS:
        return False, f"'{stream_id}' is a reserved name"
    return True, ""


def validate_stream_form(
    stream_id: str,
    video_source: str,
    timezone: str,
    detection_confidence: float,
    detection_confidence_ir: Optional[float],
    frame_interval: int,
    exit_timeout: int,
    roosting_threshold: int,
) -> list[str]:
    """
    Validate form values before anything touches disk.

    # MIRROR — mirrors the relevant subset of src/kanyo/utils/config._validate
    # (confidence ranges, frame_interval >= 1, roosting_threshold >
    # exit_timeout) so a stream created here loads cleanly in the detector.
    """
    errors = []

    valid, error = validate_stream_id(stream_id)
    if not valid:
        errors.append(error)

    if not re.match(r"^https?://", video_source or ""):
        errors.append("Video source must be an http(s) URL")

    try:
        ZoneInfo(timezone)
    except Exception:
        errors.append(f"Timezone must be a valid IANA name (got '{timezone}')")

    if not 0.0 <= detection_confidence <= 1.0:
        errors.append("detection_confidence must be between 0.0 and 1.0")

    if detection_confidence_ir is not None and not 0.0 <= detection_confidence_ir <= 1.0:
        errors.append("detection_confidence_ir must be between 0.0 and 1.0")

    if frame_interval < 1:
        errors.append("frame_interval must be at least 1")

    if exit_timeout <= 0:
        errors.append("exit_timeout must be positive")

    if roosting_threshold <= exit_timeout:
        errors.append("roosting_threshold must be greater than exit_timeout")

    return errors


# ──────────────────────────────────────────────────────────────────────────────
# Config assembly + creation
# ──────────────────────────────────────────────────────────────────────────────
def build_stream_config(
    name: str,
    video_source: str,
    timezone: str,
    short_name: str,
    location: str,
    species: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    maintainer: str = "",
    maintainer_url: str = "",
    description: str = "",
    telegram_enabled: bool = False,
    telegram_channel: str = "",
    detection_confidence: Optional[float] = None,
    detection_confidence_ir: Optional[float] = None,
    frame_interval: Optional[int] = None,
    exit_timeout: Optional[int] = None,
    roosting_threshold: Optional[int] = None,
    detect_any_animal: bool = True,
    presence_enabled: bool = True,
    significance_filter_enabled: bool = True,
    bird_count_enabled: bool = False,
) -> dict[str, Any]:
    """Template defaults overlaid with the form's values.

    Display metadata is built from the form only — the template's placeholder
    display values ("My Cam", example.com, ...) are never written to a real
    stream. Optional display fields are omitted when empty; they can be added
    later in the config editor.
    """
    config = deepcopy(TEMPLATE_DEFAULTS)

    config["stream_name"] = name
    config["video_source"] = video_source
    config["timezone"] = timezone
    config["telegram_enabled"] = telegram_enabled
    config["telegram_channel"] = telegram_channel or ""

    if detection_confidence is not None:
        config["detection_confidence"] = detection_confidence
    if detection_confidence_ir is not None:
        config["detection_confidence_ir"] = detection_confidence_ir
    if frame_interval is not None:
        config["frame_interval"] = frame_interval
    if exit_timeout is not None:
        config["exit_timeout"] = exit_timeout
    if roosting_threshold is not None:
        config["roosting_threshold"] = roosting_threshold
    config["detect_any_animal"] = detect_any_animal
    config["presence_enabled"] = presence_enabled
    config["significance_filter_enabled"] = significance_filter_enabled
    config["bird_count_enabled"] = bird_count_enabled

    display: dict[str, Any] = {
        "short_name": short_name,
        "location": location,
        "species": species,
    }
    if latitude is not None and longitude is not None:
        display["coordinates"] = [latitude, longitude]
    if maintainer:
        display["maintainer"] = maintainer
    if maintainer_url:
        display["maintainer_url"] = maintainer_url
    if description:
        display["description"] = description
    config["display"] = display

    return config


def create_stream(stream_id: str, config: dict[str, Any]) -> tuple[bool, str]:
    """
    Write the on-disk stream definition: directory, config.yaml, clips/, logs/.

    Bounded scope (issue #6): no containers are created — the caller surfaces
    the manual next steps (compose service block, .env entry, Telegram setup).

    Returns:
        Tuple of (success, message). On success the message is the created
        directory path.
    """
    stream_dir = stream_dir_for(stream_id)

    if stream_dir.exists():
        return False, f"Stream directory already exists: {stream_dir}"
    # A legacy per-stream mount with the same id also counts as taken.
    legacy_dir = settings.DATA_PATH / stream_id
    if legacy_dir.exists():
        return False, f"Stream directory already exists: {legacy_dir}"

    try:
        stream_dir.mkdir(parents=True)
        (stream_dir / "clips").mkdir()
        (stream_dir / "logs").mkdir()

        with open(stream_dir / "config.yaml", "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        return True, str(stream_dir)

    except Exception as e:
        # Roll back a partial directory so a retry starts clean.
        if stream_dir.exists():
            import shutil

            shutil.rmtree(stream_dir, ignore_errors=True)
        return False, f"Error creating stream: {e}"


def restart_admin_container() -> tuple[bool, str]:
    """Restart the admin container itself."""
    try:
        # Get our own container name
        result = subprocess.run(
            ["hostname"],
            capture_output=True,
            text=True,
        )
        container_id = result.stdout.strip()

        # Schedule restart (docker will restart us)
        # Use nohup to ensure the restart command continues after we die
        subprocess.Popen(
            ["sh", "-c", f"sleep 2 && docker restart {container_id}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        return True, "Admin restart scheduled"

    except Exception as e:
        return False, str(e)
