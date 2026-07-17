"""Tests for configuration validation rules."""

import logging
import os
from datetime import timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from kanyo.utils.config import (
    DEFAULTS,
    _apply_env_overrides,
    _cast,
    _load_env_file,
    _parse_timezone,
    _validate,
    get_now_tz,
    load_config,
)


class TestConfigValidation:
    """Test configuration validation catches illogical setups."""

    def test_roosting_threshold_must_exceed_exit_timeout(self):
        """roosting_threshold <= exit_timeout should fail."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "roosting_threshold": 300,
            "exit_timeout": 300,  # Equal - should fail
        }
        with pytest.raises(ValueError, match="roosting_threshold.*must be greater than"):
            _validate(cfg)

    def test_valid_timing_config_passes(self):
        """Valid timing configuration should pass."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "exit_timeout": 90,
            "roosting_threshold": 1800,
        }
        # Should not raise
        _validate(cfg)

    def test_detection_confidence_range(self):
        """detection_confidence must be 0.0-1.0."""
        cfg = {"video_source": "https://youtube.com/test", "detection_confidence": 1.5}
        with pytest.raises(ValueError, match="detection_confidence must be between"):
            _validate(cfg)

    def test_frame_interval_minimum(self):
        """frame_interval must be at least 1."""
        cfg = {"video_source": "https://youtube.com/test", "frame_interval": 0}
        with pytest.raises(ValueError, match="frame_interval must be at least 1"):
            _validate(cfg)

    def test_short_visit_threshold_minimum(self):
        """short_visit_threshold must be at least 60s."""
        cfg = {"video_source": "https://youtube.com/test", "short_visit_threshold": 30}
        with pytest.raises(ValueError, match="short_visit_threshold.*too short"):
            _validate(cfg)

    def test_negative_clip_timing_fails(self):
        """Negative clip timing values should fail."""
        cfg = {"video_source": "https://youtube.com/test", "clip_arrival_before": -5}
        with pytest.raises(ValueError, match="non-negative"):
            _validate(cfg)

    def test_missing_video_source_fails(self):
        """Empty or absent required field (video_source) should fail."""
        with pytest.raises(ValueError, match="Missing required config field: video_source"):
            _validate({"video_source": ""})

    def test_ir_confidence_must_be_numeric(self):
        """detection_confidence_ir must be a number when provided."""
        cfg = {"video_source": "https://youtube.com/test", "detection_confidence_ir": "low"}
        with pytest.raises(ValueError, match="detection_confidence_ir must be a number"):
            _validate(cfg)

    def test_ir_confidence_range(self):
        """detection_confidence_ir must be 0.0-1.0 when provided."""
        cfg = {"video_source": "https://youtube.com/test", "detection_confidence_ir": 1.5}
        with pytest.raises(ValueError, match="detection_confidence_ir must be between"):
            _validate(cfg)

    def test_valid_ir_confidence_passes(self):
        """A valid detection_confidence_ir passes validation."""
        cfg = {"video_source": "https://youtube.com/test", "detection_confidence_ir": 0.3}
        _validate(cfg)  # Should not raise

    def test_negative_departure_clip_timing_fails(self):
        """Negative departure clip timing values should fail."""
        cfg = {"video_source": "https://youtube.com/test", "clip_departure_after": -1}
        with pytest.raises(ValueError, match="clip_departure_before and clip_departure_after"):
            _validate(cfg)

    def test_short_arrival_clip_window_warns_but_passes(self, caplog):
        """Arrival clip windows under 10s warn but do not fail validation."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "clip_arrival_before": 2,
            "clip_arrival_after": 3,
        }
        with caplog.at_level(logging.WARNING, logger="kanyo.utils.config"):
            _validate(cfg)  # Should not raise
        assert "Arrival clip duration (5s) is very short" in caplog.text

    def test_short_departure_clip_window_warns_but_passes(self, caplog):
        """Departure clip windows under 10s warn but do not fail validation."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "clip_departure_before": 4,
            "clip_departure_after": 4,
        }
        with caplog.at_level(logging.WARNING, logger="kanyo.utils.config"):
            _validate(cfg)  # Should not raise
        assert "Departure clip duration (8s) is very short" in caplog.text

    def test_stream_read_timeout_must_be_positive(self):
        """stream_read_timeout_s of zero or below should fail."""
        cfg = {"video_source": "https://youtube.com/test", "stream_read_timeout_s": 0}
        with pytest.raises(ValueError, match="stream_read_timeout_s must be a positive"):
            _validate(cfg)

    def test_high_frame_interval_warns_but_passes(self, caplog):
        """frame_interval above 60 warns about coarse detection but passes."""
        cfg = {"video_source": "https://youtube.com/test", "frame_interval": 120}
        with caplog.at_level(logging.WARNING, logger="kanyo.utils.config"):
            _validate(cfg)  # Should not raise
        assert "frame_interval (120) is very high" in caplog.text

    def test_arrival_confirmation_seconds_must_be_positive(self):
        cfg = {"video_source": "https://youtube.com/test", "arrival_confirmation_seconds": 0}
        with pytest.raises(ValueError, match="arrival_confirmation_seconds must be positive"):
            _validate(cfg)

    def test_arrival_confirmation_ratio_range(self):
        cfg = {"video_source": "https://youtube.com/test", "arrival_confirmation_ratio": 1.5}
        with pytest.raises(ValueError, match="arrival_confirmation_ratio must be between"):
            _validate(cfg)

    def test_merge_window_must_be_non_negative(self):
        cfg = {"video_source": "https://youtube.com/test", "merge_window_seconds": -1}
        with pytest.raises(ValueError, match="merge_window_seconds must be >= 0"):
            _validate(cfg)

    def test_min_significant_seconds_must_be_non_negative(self):
        cfg = {"video_source": "https://youtube.com/test", "min_significant_seconds": -5}
        with pytest.raises(ValueError, match="min_significant_seconds must be >= 0"):
            _validate(cfg)

    def test_damping_arrivals_threshold_must_be_integer(self):
        cfg = {"video_source": "https://youtube.com/test", "damping_arrivals_threshold": 2.5}
        with pytest.raises(ValueError, match="damping_arrivals_threshold must be an integer"):
            _validate(cfg)

    def test_damping_window_hours_must_be_positive(self):
        cfg = {"video_source": "https://youtube.com/test", "damping_window_hours": 0}
        with pytest.raises(ValueError, match="damping_window_hours must be a positive"):
            _validate(cfg)

    def test_presence_fraction_keys_range(self):
        cfg = {"video_source": "https://youtube.com/test", "presence_sustain_confidence": 1.5}
        with pytest.raises(ValueError, match="presence_sustain_confidence must be between"):
            _validate(cfg)

    def test_presence_motion_pixel_threshold_range(self):
        cfg = {"video_source": "https://youtube.com/test", "presence_motion_pixel_threshold": 300}
        with pytest.raises(ValueError, match="presence_motion_pixel_threshold must be between"):
            _validate(cfg)

    def test_presence_failsafe_must_exceed_exit_timeout(self):
        """The absence failsafe must not race the exit_timeout debounce."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "presence_enabled": True,
            "exit_timeout": 300,
            "presence_absence_failsafe_seconds": 300,
        }
        with pytest.raises(ValueError, match="presence_absence_failsafe_seconds"):
            _validate(cfg)

    def test_presence_failsafe_not_enforced_when_presence_disabled(self):
        """With the presence layer off, the failsafe key is inert."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "presence_enabled": False,
            "exit_timeout": 300,
            "presence_absence_failsafe_seconds": 60,
        }
        _validate(cfg)  # Should not raise


class TestGetNowTz:
    """Behavior of get_now_tz timezone-aware clock."""

    def test_uses_configured_timezone_obj(self):
        tz = ZoneInfo("America/Chicago")
        now = get_now_tz({"timezone_obj": tz})
        assert now.tzinfo is tz

    def test_defaults_to_utc(self):
        now = get_now_tz({})
        assert now.tzinfo is timezone.utc


class TestLoadEnvFile:
    """Behavior of the minimal .env loader."""

    def test_missing_file_is_a_noop(self, tmp_path):
        """A nonexistent .env path does nothing."""
        before = dict(os.environ)
        _load_env_file(tmp_path / "nope.env")
        assert dict(os.environ) == before

    def test_parses_key_value_pairs(self, tmp_path):
        """KEY=VALUE lines land in os.environ; comments and blanks are skipped."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# a comment\n"
            "\n"
            "KANYO_TEST_ALPHA = hello \n"
            "KANYO_TEST_BETA=with=equals\n"
            "NOT_A_PAIR\n"
            "=orphan_value\n"
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KANYO_TEST_ALPHA", None)
            os.environ.pop("KANYO_TEST_BETA", None)
            _load_env_file(env_file)
            assert os.environ["KANYO_TEST_ALPHA"] == "hello"
            # partition keeps everything after the first '='
            assert os.environ["KANYO_TEST_BETA"] == "with=equals"
            assert "NOT_A_PAIR" not in os.environ
            assert "" not in os.environ

    def test_existing_env_vars_not_overridden(self, tmp_path):
        """Real environment variables win over .env file values."""
        env_file = tmp_path / ".env"
        env_file.write_text("KANYO_TEST_GAMMA=from_file\n")
        with patch.dict(os.environ, {"KANYO_TEST_GAMMA": "from_env"}):
            _load_env_file(env_file)
            assert os.environ["KANYO_TEST_GAMMA"] == "from_env"


class TestCast:
    """Behavior of env-var string casting against a reference default."""

    def test_bool_truthy_values(self):
        assert _cast("1", True) is True
        assert _cast("true", False) is True
        assert _cast("YES", False) is True

    def test_bool_falsy_values(self):
        assert _cast("0", True) is False
        assert _cast("no", True) is False
        assert _cast("anything-else", True) is False

    def test_int(self):
        assert _cast("42", 5) == 42

    def test_float(self):
        assert _cast("0.75", 0.5) == 0.75

    def test_string_passthrough(self):
        assert _cast("rtsp://cam", "") == "rtsp://cam"


class TestApplyEnvOverrides:
    """Behavior of KANYO_<KEY> environment overrides."""

    def test_overrides_typed_values(self, monkeypatch):
        """Env values are cast to the default's type before overriding."""
        monkeypatch.setenv("KANYO_DETECTION_CONFIDENCE", "0.75")
        monkeypatch.setenv("KANYO_FRAME_INTERVAL", "10")
        monkeypatch.setenv("KANYO_TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("KANYO_VIDEO_SOURCE", "rtsp://cam")
        cfg = DEFAULTS.copy()

        _apply_env_overrides(cfg)

        assert cfg["detection_confidence"] == 0.75
        assert cfg["frame_interval"] == 10
        assert cfg["telegram_enabled"] is True
        assert cfg["video_source"] == "rtsp://cam"

    def test_empty_env_value_ignored(self, monkeypatch):
        """Empty env values do not clobber config values."""
        monkeypatch.setenv("KANYO_FRAME_INTERVAL", "")
        cfg = DEFAULTS.copy()
        cfg["frame_interval"] = 15

        _apply_env_overrides(cfg)

        assert cfg["frame_interval"] == 15

    def test_unset_env_leaves_config_alone(self, monkeypatch):
        """Keys without a matching env var are untouched."""
        monkeypatch.delenv("KANYO_DETECTION_CONFIDENCE", raising=False)
        cfg = DEFAULTS.copy()

        _apply_env_overrides(cfg)

        assert cfg["detection_confidence"] == DEFAULTS["detection_confidence"]


class TestParseTimezone:
    """Behavior of timezone string parsing."""

    def test_empty_and_utc_forms(self):
        assert _parse_timezone("") == ZoneInfo("UTC")
        assert _parse_timezone("UTC") == ZoneInfo("UTC")
        assert _parse_timezone("+00:00") == ZoneInfo("UTC")

    def test_iana_name(self):
        assert _parse_timezone("America/New_York") == ZoneInfo("America/New_York")

    def test_invalid_iana_name_falls_back_to_utc(self):
        """A slash-form name that isn't a real zone falls back to UTC."""
        assert _parse_timezone("Not/AZone") == timezone.utc

    def test_legacy_offset_maps_to_iana(self):
        """Known legacy offsets map to DST-aware IANA zones."""
        assert _parse_timezone("-05:00") == ZoneInfo("America/New_York")

    def test_unmapped_offset_becomes_fixed_offset(self):
        """Offsets with no IANA mapping become fixed-offset timezones."""
        tz = _parse_timezone("+03:30")
        assert tz == timezone(timedelta(hours=3, minutes=30))

    def test_negative_unmapped_offset(self):
        """Negative unmapped offsets keep their sign on hours and minutes."""
        tz = _parse_timezone("-03:30")
        assert tz == timezone(timedelta(hours=-3, minutes=-30))

    def test_malformed_offset_falls_back_to_utc(self):
        """An offset that can't be parsed as numbers falls back to UTC."""
        assert _parse_timezone("+ab:cd") == timezone.utc

    def test_unrecognized_format_falls_back_to_utc(self):
        assert _parse_timezone("banana") == timezone.utc


class TestLoadConfig:
    """Behavior of the full load_config pipeline: env > YAML > defaults."""

    def test_yaml_overrides_defaults(self, tmp_path, monkeypatch):
        """YAML values override defaults; unlisted keys keep their defaults."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text(
            "video_source: https://youtube.com/test\ndetection_confidence: 0.8\n"
        )

        cfg = load_config("config.yaml")

        assert cfg["video_source"] == "https://youtube.com/test"
        assert cfg["detection_confidence"] == 0.8
        assert cfg["frame_interval"] == DEFAULTS["frame_interval"]
        assert cfg["timezone_obj"] == ZoneInfo("UTC")

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        """KANYO_* env vars take priority over YAML values."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text(
            "video_source: https://youtube.com/test\ndetection_confidence: 0.8\n"
        )
        monkeypatch.setenv("KANYO_DETECTION_CONFIDENCE", "0.9")

        cfg = load_config("config.yaml")

        assert cfg["detection_confidence"] == 0.9

    def test_missing_yaml_uses_defaults_and_env(self, tmp_path, monkeypatch):
        """Without a YAML file, defaults plus env overrides are used."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("KANYO_VIDEO_SOURCE", "rtsp://cam")

        cfg = load_config("does-not-exist.yaml")

        assert cfg["video_source"] == "rtsp://cam"
        assert cfg["detection_confidence"] == DEFAULTS["detection_confidence"]

    def test_empty_yaml_treated_as_empty_mapping(self, tmp_path, monkeypatch):
        """An empty YAML file is treated as {} rather than crashing."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("")
        monkeypatch.setenv("KANYO_VIDEO_SOURCE", "rtsp://cam")

        cfg = load_config("config.yaml")

        assert cfg["video_source"] == "rtsp://cam"

    def test_dotenv_file_loaded(self, tmp_path, monkeypatch):
        """A .env file in the working directory is loaded into the environment."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("video_source: https://youtube.com/test\n")
        (tmp_path / ".env").write_text("KANYO_TEST_SECRET=hunter2\n")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KANYO_TEST_SECRET", None)
            load_config("config.yaml")
            assert os.environ["KANYO_TEST_SECRET"] == "hunter2"

    def test_timezone_string_parsed_into_timezone_obj(self, tmp_path, monkeypatch):
        """A timezone string in YAML is parsed into a tzinfo object."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text(
            "video_source: https://youtube.com/test\ntimezone: America/Chicago\n"
        )

        cfg = load_config("config.yaml")

        assert cfg["timezone_obj"] == ZoneInfo("America/Chicago")

    def test_non_string_timezone_defaults_to_utc(self, tmp_path, monkeypatch):
        """A non-string timezone value yields a UTC timezone_obj."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text(
            "video_source: https://youtube.com/test\ntimezone: null\n"
        )

        cfg = load_config("config.yaml")

        assert cfg["timezone_obj"] == timezone.utc

    def test_invalid_config_raises(self, tmp_path, monkeypatch):
        """Validation failures surface as ValueError from load_config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text(
            "video_source: https://youtube.com/test\ndetection_confidence: 2.0\n"
        )

        with pytest.raises(ValueError, match="detection_confidence must be between"):
            load_config("config.yaml")
