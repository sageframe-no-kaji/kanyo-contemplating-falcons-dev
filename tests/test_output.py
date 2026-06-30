"""Tests for output utilities (format_duration, get_output_path, save_thumbnail)."""
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, Mock

import numpy as np
import pytest

from kanyo.utils.output import format_duration, get_output_path, save_thumbnail


class TestFormatDuration:
    def test_seconds_only(self):
        assert format_duration(45) == "45s"
        assert format_duration(0) == "0s"
        assert format_duration(59) == "59s"

    def test_minutes_and_seconds(self):
        assert format_duration(60) == "1m"
        assert format_duration(61) == "1m 1s"
        assert format_duration(125) == "2m 5s"
        assert format_duration(3599) == "59m 59s"

    def test_minutes_exact(self):
        assert format_duration(120) == "2m"
        assert format_duration(180) == "3m"

    def test_hours_and_minutes(self):
        assert format_duration(3600) == "1h"
        assert format_duration(3660) == "1h 1m"
        assert format_duration(3665) == "1h 1m"
        assert format_duration(7200) == "2h"
        assert format_duration(7500) == "2h 5m"

    def test_float_input(self):
        assert format_duration(90.5) == "1m 30s"


class TestGetOutputPath:
    def test_creates_date_dir_and_correct_filename(self, tmp_path):
        # microsecond=0 → "000000" in the %f slot (021-I format)
        ts = datetime(2026, 2, 26, 14, 30, 25)
        result = get_output_path(str(tmp_path), ts, "arrival", "mp4")
        expected_dir = tmp_path / "2026-02-26"
        assert expected_dir.exists()
        assert result == expected_dir / "falcon_143025_000000_arrival.mp4"

    def test_different_event_types(self, tmp_path):
        ts = datetime(2026, 2, 26, 8, 0, 0)
        for event_type, ext in [("departure", "mp4"), ("visit", "mp4"), ("arrival", "jpg")]:
            path = get_output_path(str(tmp_path), ts, event_type, ext)
            assert path.name == f"falcon_080000_000000_{event_type}.{ext}"

    def test_returns_path_object(self, tmp_path):
        ts = datetime(2026, 2, 26, 10, 0, 0)
        result = get_output_path(str(tmp_path), ts, "arrival", "jpg")
        assert isinstance(result, Path)

    def test_same_second_different_microsecond_no_collision(self, tmp_path):
        """021-I: two events with the same second but different microseconds
        must produce different paths."""
        ts1 = datetime(2026, 2, 26, 14, 30, 25, 100000)
        ts2 = datetime(2026, 2, 26, 14, 30, 25, 200000)
        p1 = get_output_path(str(tmp_path), ts1, "arrival", "mp4")
        p2 = get_output_path(str(tmp_path), ts2, "arrival", "mp4")
        assert p1 != p2
        assert p1.name == "falcon_143025_100000_arrival.mp4"
        assert p2.name == "falcon_143025_200000_arrival.mp4"

    def test_microsecond_is_zero_padded_six_digits(self, tmp_path):
        """Ensure the microsecond slot is exactly 6 digits (zero-padded)."""
        ts = datetime(2026, 2, 26, 14, 30, 25, 42)  # 42 microseconds
        result = get_output_path(str(tmp_path), ts, "visit", "mp4")
        # %f always pads to 6 digits
        assert result.name == "falcon_143025_000042_visit.mp4"


class TestSaveThumbnail:
    def test_saves_jpeg_to_correct_path(self, tmp_path):
        """save_thumbnail writes a JPEG and returns its path."""
        ts = datetime(2026, 2, 26, 10, 0, 0)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        result = save_thumbnail(frame, str(tmp_path), ts, "arrival")

        path = Path(result)
        assert path.exists()
        assert path.suffix == ".jpg"
        assert "arrival" in path.name

    def test_returns_string_path(self, tmp_path):
        ts = datetime(2026, 2, 26, 10, 0, 0)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = save_thumbnail(frame, str(tmp_path), ts, "departure")
        assert isinstance(result, str)
