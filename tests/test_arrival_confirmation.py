"""Tests for arrival confirmation system."""

from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kanyo.detection.event_types import FalconEvent
from kanyo.detection.falcon_state import FalconStateMachine


class TestArrivalConfirmation:
    """Test arrival confirmation logic."""

    def test_config_defaults(self):
        """Test that arrival confirmation config has correct defaults."""
        from kanyo.utils.config import DEFAULTS

        assert "arrival_confirmation_seconds" in DEFAULTS
        assert "arrival_confirmation_ratio" in DEFAULTS
        assert DEFAULTS["arrival_confirmation_seconds"] == 10
        assert DEFAULTS["arrival_confirmation_ratio"] == 0.3

    def test_config_validation_seconds_positive(self):
        """Test that arrival_confirmation_seconds must be positive."""
        from kanyo.utils.config import _validate

        config = {
            "video_source": "test",
            "arrival_confirmation_seconds": -1,
            "roosting_threshold": 2000,
            "exit_timeout": 300,
        }

        with pytest.raises(ValueError, match="arrival_confirmation_seconds must be positive"):
            _validate(config)

    def test_config_validation_ratio_range(self):
        """Test that arrival_confirmation_ratio must be between 0 and 1."""
        from kanyo.utils.config import _validate

        config = {
            "video_source": "test",
            "arrival_confirmation_ratio": 1.5,
            "roosting_threshold": 2000,
            "exit_timeout": 300,
        }

        with pytest.raises(
            ValueError, match="arrival_confirmation_ratio must be between 0.0 and 1.0"
        ):
            _validate(config)

    def test_state_machine_reset_to_absent(self):
        """Test that reset_to_absent() properly resets state machine."""
        config = {"exit_timeout": 90, "roosting_threshold": 1800}
        state_machine = FalconStateMachine(config)

        # Set up some state
        now = datetime.now()
        state_machine.update(True, now)  # Trigger ARRIVED
        assert state_machine.state.value == "visiting"
        assert state_machine.visit_start is not None

        # Reset to absent
        state_machine.reset_to_absent()

        assert state_machine.state.value == "absent"
        assert state_machine.visit_start is None
        assert state_machine.last_detection is None

    @patch("kanyo.utils.output.cv2")
    def test_save_thumbnail(self, mock_cv2):
        """Test that save_thumbnail creates .jpg file."""
        from kanyo.utils.output import save_thumbnail

        mock_frame = Mock()
        timestamp = datetime(2026, 1, 3, 10, 30, 0)

        # Test normal operation
        result_path = save_thumbnail(mock_frame, "clips", timestamp, "arrival")

        assert result_path.endswith(".jpg")
        assert "falcon_103000_arrival" in result_path

    def test_visit_recorder_rename_to_final(self, tmp_path):
        """Test that visit recorder can rename .tmp file to final."""
        from kanyo.utils.visit_recorder import VisitRecorder

        recorder = VisitRecorder(clips_dir=str(tmp_path), fps=30)

        # Create a fake .tmp file
        tmp_file = tmp_path / "2026-01-03" / "falcon_103000_visit.mp4.tmp"
        tmp_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text("test")

        # Simulate internal state
        recorder._visit_path = tmp_file
        recorder._final_path = tmp_path / "2026-01-03" / "falcon_103000_visit.mp4"

        # Rename
        final_path = recorder.rename_to_final()

        assert final_path is not None
        assert final_path.exists()
        assert not tmp_file.exists()
        assert str(final_path).endswith(".mp4")
        assert not str(final_path).endswith(".tmp")

    def test_arrival_clip_recorder_rename_to_final(self):
        """Test that arrival clip recorder delegates rename to visit recorder."""
        from kanyo.utils.arrival_clip_recorder import ArrivalClipRecorder

        mock_clip_manager = Mock()
        recorder = ArrivalClipRecorder(mock_clip_manager)

        # Mock internal recorder
        mock_visit_recorder = Mock()
        mock_visit_recorder.rename_to_final.return_value = Path("/fake/path.mp4")
        recorder._recorder = mock_visit_recorder

        # Call rename
        result = recorder.rename_to_final()

        assert result == Path("/fake/path.mp4")
        mock_visit_recorder.rename_to_final.assert_called_once()

    def test_buffer_monitor_arrival_pending_initialization(self):
        """Test that BufferMonitor initializes arrival pending state correctly."""
        from kanyo.detection.buffer_monitor import BufferMonitor

        monitor = BufferMonitor(
            stream_url="test",
            full_config={"arrival_confirmation_seconds": 15, "arrival_confirmation_ratio": 0.4},
        )

        assert monitor.arrival_pending is False
        assert monitor.arrival_pending_start is None
        assert monitor.arrival_detection_count == 0
        assert monitor.arrival_frame_count == 0
        assert monitor.arrival_confirmation_seconds == 15
        assert monitor.arrival_confirmation_ratio == 0.4


class TestArrivalConfirmationIntegration:
    """Integration tests for arrival confirmation workflow."""

    @patch("kanyo.detection.buffer_monitor.StreamCapture")
    @patch("kanyo.detection.buffer_monitor.FalconDetector")
    @patch("kanyo.utils.output.cv2")
    def test_successful_arrival_confirmation(self, mock_cv2, mock_detector_cls, mock_capture_cls):
        """Test that sustained detections trigger confirmation and notification."""
        from kanyo.detection.buffer_monitor import BufferMonitor

        # Set up monitor with short confirmation window for testing
        monitor = BufferMonitor(
            stream_url="test",
            full_config={
                "arrival_confirmation_seconds": 2,
                "arrival_confirmation_ratio": 0.5,
            },
        )

        # Mock event handler
        monitor.event_handler.handle_event = Mock()
        monitor.event_handler.last_frame = Mock()

        # Simulate ARRIVED event
        now = datetime.now()
        monitor._handle_event(FalconEvent.ARRIVED, now, {})

        # Check pending state is set
        assert monitor.arrival_pending is True
        assert monitor.arrival_pending_start == now
        assert monitor.arrival_detection_count == 1
        assert monitor.arrival_frame_count == 1

        # Event handler should NOT have been called yet
        monitor.event_handler.handle_event.assert_not_called()

    @patch("kanyo.detection.buffer_monitor.StreamCapture")
    @patch("kanyo.detection.buffer_monitor.FalconDetector")
    @patch("kanyo.utils.output.cv2")
    def test_failed_arrival_confirmation(self, mock_cv2, mock_detector_cls, mock_capture_cls):
        """Test that insufficient detections cancel arrival."""
        from kanyo.detection.buffer_monitor import BufferMonitor

        monitor = BufferMonitor(
            stream_url="test",
            full_config={
                "arrival_confirmation_seconds": 2,
                "arrival_confirmation_ratio": 0.5,
            },
        )

        # Mock components
        monitor.event_handler.handle_event = Mock()
        monitor.event_handler.last_frame = Mock()
        monitor.state_machine.reset_to_absent = Mock()
        monitor.arrival_clip_recorder.stop_recording = Mock()
        monitor.visit_recorder.stop_recording = Mock()

        # Simulate ARRIVED event
        now = datetime.now()
        monitor._handle_event(FalconEvent.ARRIVED, now, {})

        # Simulate low detection rate (20% < 50% threshold)
        monitor.arrival_detection_count = 2
        monitor.arrival_frame_count = 10

        # Trigger cancellation
        monitor._cancel_arrival(0.2)

        # Should reset state machine
        monitor.state_machine.reset_to_absent.assert_called_once()

        # Should stop recordings
        monitor.arrival_clip_recorder.stop_recording.assert_called_once()
        monitor.visit_recorder.stop_recording.assert_called_once()

        # Pending state should be cleared
        assert monitor.arrival_pending is False
        assert monitor.arrival_pending_start is None
