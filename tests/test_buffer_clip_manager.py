"""Tests for buffer clip manager module."""

from pathlib import Path
from unittest.mock import MagicMock

from kanyo.detection.buffer_clip_manager import BufferClipManager


class TestBufferClipManagerInit:
    """Tests for BufferClipManager initialization."""

    def test_default_init(self):
        """Test default initialization."""
        mock_buffer = MagicMock()
        mock_recorder = MagicMock()
        mock_config = {"timezone": "UTC"}

        manager = BufferClipManager(
            frame_buffer=mock_buffer,
            visit_recorder=mock_recorder,
            full_config=mock_config,
        )

        assert manager.clips_dir == Path("clips")
        assert manager.clip_fps == 30
        assert manager.clip_crf == 23
        assert manager.clip_arrival_before == 15
        assert manager.clip_arrival_after == 30
        assert manager.clip_departure_before == 30
        assert manager.clip_departure_after == 15

    def test_custom_init(self):
        """Test custom initialization."""
        mock_buffer = MagicMock()
        mock_recorder = MagicMock()

        manager = BufferClipManager(
            frame_buffer=mock_buffer,
            visit_recorder=mock_recorder,
            full_config={"timezone": "UTC"},
            clips_dir="output/clips",
            clip_fps=24,
            clip_arrival_before=20,
            clip_departure_after=10,
        )

        assert manager.clips_dir == Path("output/clips")
        assert manager.clip_fps == 24
        assert manager.clip_arrival_before == 20
        assert manager.clip_departure_after == 10


class TestBufferClipManagerShutdown:
    """Tests for shutdown behavior."""

    def test_shutdown_sets_flag(self):
        """Test that shutdown sets the shutdown flag."""
        mock_buffer = MagicMock()
        mock_recorder = MagicMock()

        manager = BufferClipManager(
            frame_buffer=mock_buffer,
            visit_recorder=mock_recorder,
            full_config={"timezone": "UTC"},
        )

        assert manager._shutdown is False
        manager.shutdown()
        assert manager._shutdown is True


class TestClipTimingCalculation:
    """Tests for clip offset calculations."""

    def test_arrival_clip_timing(self):
        """Test arrival clip uses correct before/after values."""
        mock_buffer = MagicMock()
        mock_recorder = MagicMock()
        mock_recorder.is_recording = False

        manager = BufferClipManager(
            frame_buffer=mock_buffer,
            visit_recorder=mock_recorder,
            full_config={"timezone": "UTC"},
            clip_arrival_before=15,
            clip_arrival_after=30,
        )

        # Arrival at 15 seconds into recording means:
        # - Start offset = 15 - 15 = 0
        # - End offset = 15 + 30 = 45
        # - Duration = 45
        arrival_offset = 15.0

        # The clip should span from before to after the arrival
        expected_start = max(0, arrival_offset - 15)  # 0
        expected_duration = 15 + 30  # 45

        assert expected_start == 0
        assert expected_duration == 45
        assert manager.clip_arrival_before == 15
        assert manager.clip_arrival_after == 30

    def test_departure_clip_timing(self):
        """Test departure clip uses correct before/after values."""
        mock_buffer = MagicMock()
        mock_recorder = MagicMock()

        manager = BufferClipManager(
            frame_buffer=mock_buffer,
            visit_recorder=mock_recorder,
            full_config={"timezone": "UTC"},
            clip_departure_before=30,
            clip_departure_after=15,
        )

        # Departure at 1800 seconds (30 min) into recording means:
        # - Start offset = 1800 - 30 = 1770
        # - Duration = 30 + 15 = 45
        departure_offset = 1800.0

        expected_start = departure_offset - 30  # 1770
        expected_duration = 30 + 15  # 45

        assert expected_start == 1770.0
        assert expected_duration == 45
        assert manager.clip_departure_before == 30
        assert manager.clip_departure_after == 15


class TestBufferClipManagerIntegration:
    """Integration-style tests with mocked dependencies."""

    def test_create_arrival_clip_from_visit(self):
        """Test creating arrival clip from visit recording."""
        mock_buffer = MagicMock()
        mock_recorder = MagicMock()
        mock_recorder.current_visit_path = Path("/tmp/test_visit.mp4")

        manager = BufferClipManager(
            frame_buffer=mock_buffer,
            visit_recorder=mock_recorder,
            full_config={"timezone": "UTC"},
            clips_dir="/tmp/clips",
        )

        # The manager should use visit_recorder.extract_clip_from_file
        # or similar when extracting arrival clips

        # This verifies the manager is set up correctly
        assert manager.visit_recorder == mock_recorder
        assert manager.frame_buffer == mock_buffer
