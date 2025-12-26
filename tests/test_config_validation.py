"""Tests for configuration validation rules."""

import pytest

from kanyo.utils.config import _validate


class TestConfigValidation:
    """Test configuration validation catches illogical setups."""

    def test_activity_timeout_must_be_less_than_roosting_exit(self):
        """activity_timeout >= roosting_exit_timeout should fail."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "activity_timeout": 600,
            "roosting_exit_timeout": 600,  # Equal - should fail
        }
        with pytest.raises(ValueError, match="activity_timeout.*must be less than"):
            _validate(cfg)

    def test_exit_timeout_must_be_less_than_roosting_exit(self):
        """exit_timeout >= roosting_exit_timeout should fail."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "exit_timeout": 700,
            "roosting_exit_timeout": 600,  # Less than exit - should fail
        }
        with pytest.raises(ValueError, match="exit_timeout.*must be less than"):
            _validate(cfg)

    def test_roosting_threshold_must_exceed_exit_timeout(self):
        """roosting_threshold <= exit_timeout should fail."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "roosting_threshold": 300,
            "exit_timeout": 300,  # Equal - should fail
        }
        with pytest.raises(
            ValueError, match="roosting_threshold.*must be greater than"
        ):
            _validate(cfg)

    def test_valid_timing_config_passes(self):
        """Valid timing configuration should pass."""
        cfg = {
            "video_source": "https://youtube.com/test",
            "exit_timeout": 300,
            "roosting_threshold": 1800,
            "roosting_exit_timeout": 600,
            "activity_timeout": 180,
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
