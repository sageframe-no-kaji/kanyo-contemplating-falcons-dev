"""Tests for roosting mode (stop recording + reduced YOLO polling)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.detection.event_types import FalconEvent


def make_monitor(roosting_recording_mode: str = "stop", roosting_detection_interval: int = 30):
    """Return a BufferMonitor with all external dependencies mocked out."""
    with (
        patch("kanyo.detection.buffer_monitor.StreamCapture"),
        patch("kanyo.detection.buffer_monitor.FalconDetector"),
        patch("kanyo.detection.buffer_monitor.FrameBuffer"),
        patch("kanyo.detection.buffer_monitor.VisitRecorder"),
        patch("kanyo.detection.buffer_monitor.BufferClipManager"),
        patch("kanyo.detection.buffer_monitor.EventStore"),
        patch("kanyo.detection.buffer_monitor.FalconEventHandler"),
        patch("kanyo.detection.buffer_monitor.FalconStateMachine"),
        patch("kanyo.detection.buffer_monitor.ArrivalClipRecorder"),
    ):
        monitor = BufferMonitor(
            stream_url="test",
            full_config={
                "roosting_recording_mode": roosting_recording_mode,
                "roosting_detection_interval": roosting_detection_interval,
                "arrival_confirmation_seconds": 10,
                "arrival_confirmation_ratio": 0.3,
            },
        )
    return monitor


class TestRoostingModeInit:
    def test_defaults_continuous(self):
        monitor = make_monitor(roosting_recording_mode="continuous")
        assert monitor.roosting_recording_mode == "continuous"
        assert monitor.roosting_detection_interval == 30
        assert monitor.roosting_mode_active is False
        assert monitor.last_roosting_check is None
        assert monitor._roosting_visit_metadata is None

    def test_stop_mode_config_read(self):
        monitor = make_monitor(roosting_recording_mode="stop", roosting_detection_interval=60)
        assert monitor.roosting_recording_mode == "stop"
        assert monitor.roosting_detection_interval == 60

    def test_default_config_is_continuous(self):
        """BufferMonitor with no roosting config defaults to continuous."""
        with (
            patch("kanyo.detection.buffer_monitor.StreamCapture"),
            patch("kanyo.detection.buffer_monitor.FalconDetector"),
            patch("kanyo.detection.buffer_monitor.FrameBuffer"),
            patch("kanyo.detection.buffer_monitor.VisitRecorder"),
            patch("kanyo.detection.buffer_monitor.BufferClipManager"),
            patch("kanyo.detection.buffer_monitor.EventStore"),
            patch("kanyo.detection.buffer_monitor.FalconEventHandler"),
            patch("kanyo.detection.buffer_monitor.FalconStateMachine"),
            patch("kanyo.detection.buffer_monitor.ArrivalClipRecorder"),
        ):
            monitor = BufferMonitor(stream_url="test")
        assert monitor.roosting_recording_mode == "continuous"


class TestRoostingEventHandler:
    def test_roosting_stop_mode_stops_recorder(self):
        """ROOSTING in stop mode calls stop_recording on the visit recorder."""
        monitor = make_monitor(roosting_recording_mode="stop")
        now = datetime(2026, 4, 21, 10, 0, 0)

        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stop_recording.return_value = ("/fake/visit.mp4", {"visit_start": now})

        monitor._handle_event(FalconEvent.ROOSTING, now, {})

        monitor.visit_recorder.stop_recording.assert_called_once_with(now)

    def test_roosting_stop_mode_sets_active_flag(self):
        """ROOSTING in stop mode sets roosting_mode_active = True."""
        monitor = make_monitor(roosting_recording_mode="stop")
        now = datetime(2026, 4, 21, 10, 0, 0)

        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stop_recording.return_value = ("/fake/visit.mp4", {"visit_start": now})

        monitor._handle_event(FalconEvent.ROOSTING, now, {})

        assert monitor.roosting_mode_active is True
        assert monitor.last_roosting_check == now

    def test_roosting_stop_mode_caches_metadata(self):
        """ROOSTING in stop mode caches visit_metadata for later use on DEPARTED."""
        monitor = make_monitor(roosting_recording_mode="stop")
        now = datetime(2026, 4, 21, 10, 0, 0)
        fake_metadata = {"visit_start": now, "duration_seconds": 1800}

        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stop_recording.return_value = ("/fake/visit.mp4", fake_metadata)

        monitor._handle_event(FalconEvent.ROOSTING, now, {})

        assert monitor._roosting_visit_metadata == fake_metadata

    def test_roosting_continuous_mode_does_not_stop_recorder(self):
        """ROOSTING in continuous mode does NOT call stop_recording."""
        monitor = make_monitor(roosting_recording_mode="continuous")
        now = datetime(2026, 4, 21, 10, 0, 0)

        monitor.visit_recorder.is_recording = True

        monitor._handle_event(FalconEvent.ROOSTING, now, {})

        monitor.visit_recorder.stop_recording.assert_not_called()
        assert monitor.roosting_mode_active is False

    def test_roosting_continuous_mode_logs_event(self):
        """ROOSTING in continuous mode calls log_event on the visit recorder."""
        monitor = make_monitor(roosting_recording_mode="continuous")
        now = datetime(2026, 4, 21, 10, 0, 0)

        monitor.visit_recorder.is_recording = True

        monitor._handle_event(FalconEvent.ROOSTING, now, {"key": "val"})

        monitor.visit_recorder.log_event.assert_called_once_with("ROOSTING", now, {"key": "val"})


class TestRoostingYOLOGating:
    """Test that YOLO is skipped at reduced interval during roosting stop mode."""

    def _make_frame(self):
        import numpy as np
        return np.zeros((720, 1280, 3), dtype="uint8")

    def test_yolo_skipped_within_interval(self):
        """process_frame returns early (no YOLO) when interval not elapsed."""
        monitor = make_monitor(roosting_recording_mode="stop", roosting_detection_interval=30)
        frame = self._make_frame()

        now = datetime(2026, 4, 21, 10, 0, 0)
        monitor.roosting_mode_active = True
        monitor.last_roosting_check = now  # just checked

        # Call process_frame 5 seconds later
        with patch("kanyo.detection.buffer_monitor.get_now_tz", return_value=now + timedelta(seconds=5)):
            monitor.process_frame(frame, frame_number=1)

        monitor.detector.detect_birds.assert_not_called()

    def test_yolo_runs_when_interval_elapsed(self):
        """process_frame runs YOLO when roosting interval has elapsed."""
        monitor = make_monitor(roosting_recording_mode="stop", roosting_detection_interval=30)
        frame = self._make_frame()

        now = datetime(2026, 4, 21, 10, 0, 0)
        monitor.roosting_mode_active = True
        monitor.last_roosting_check = now - timedelta(seconds=31)

        monitor.detector.detect_birds.return_value = []
        monitor.state_machine.update.return_value = []
        monitor.visit_recorder.is_recording = False
        monitor.arrival_clip_recorder.is_recording.return_value = False
        monitor.frame_buffer.add_frame.return_value = None

        with patch("kanyo.detection.buffer_monitor.get_now_tz", return_value=now):
            monitor.process_frame(frame, frame_number=1)

        monitor.detector.detect_birds.assert_called_once()

    def test_yolo_not_gated_when_not_roosting(self):
        """process_frame runs YOLO normally when not in roosting mode."""
        monitor = make_monitor(roosting_recording_mode="stop")
        frame = self._make_frame()

        now = datetime(2026, 4, 21, 10, 0, 0)
        monitor.roosting_mode_active = False  # not roosting

        monitor.detector.detect_birds.return_value = []
        monitor.state_machine.update.return_value = []
        monitor.visit_recorder.is_recording = False
        monitor.arrival_clip_recorder.is_recording.return_value = False
        monitor.frame_buffer.add_frame.return_value = None

        with patch("kanyo.detection.buffer_monitor.get_now_tz", return_value=now):
            monitor.process_frame(frame, frame_number=1)

        monitor.detector.detect_birds.assert_called_once()


class TestRoostingDeparture:
    def test_departed_from_roost_uses_buffer_clip(self):
        """DEPARTED in roosting stop mode calls create_clip_from_buffer, not create_departure_clip."""
        monitor = make_monitor(roosting_recording_mode="stop")
        now = datetime(2026, 4, 21, 11, 0, 0)
        arrival_time = datetime(2026, 4, 21, 10, 0, 0)

        monitor.roosting_mode_active = True
        monitor._roosting_visit_metadata = {"visit_start": arrival_time, "duration_seconds": 3600}
        monitor.clips_dir = "clips"
        monitor.clip_manager.clip_departure_before = 30
        monitor.clip_manager.clip_departure_after = 15

        metadata = {
            "visit_start": arrival_time,
            "visit_end": now - timedelta(seconds=5),
        }

        with patch("kanyo.detection.buffer_monitor.get_output_path") as mock_path:
            mock_path.return_value = MagicMock(exists=lambda: False)
            monitor._handle_event(FalconEvent.DEPARTED, now, metadata)

        monitor.clip_manager.create_clip_from_buffer.assert_called_once_with(
            now, "departure",
            before_seconds=30,
            after_seconds=15,
        )
        monitor.clip_manager.create_departure_clip.assert_not_called()

    def test_departed_from_roost_resets_state(self):
        """DEPARTED in roosting stop mode resets roosting_mode_active and related state."""
        monitor = make_monitor(roosting_recording_mode="stop")
        now = datetime(2026, 4, 21, 11, 0, 0)
        arrival_time = datetime(2026, 4, 21, 10, 0, 0)

        monitor.roosting_mode_active = True
        monitor.last_roosting_check = now - timedelta(seconds=10)
        monitor._roosting_visit_metadata = {"visit_start": arrival_time}
        monitor.clip_manager.clip_departure_before = 30
        monitor.clip_manager.clip_departure_after = 15

        metadata = {"visit_start": arrival_time, "visit_end": now}

        with patch("kanyo.detection.buffer_monitor.get_output_path") as mock_path:
            mock_path.return_value = MagicMock(exists=lambda: False)
            monitor._handle_event(FalconEvent.DEPARTED, now, metadata)

        assert monitor.roosting_mode_active is False
        assert monitor.last_roosting_check is None
        assert monitor._roosting_visit_metadata is None

    def test_departed_normal_path_not_affected(self):
        """DEPARTED in continuous mode (roosting_mode_active=False) uses normal path."""
        monitor = make_monitor(roosting_recording_mode="continuous")
        now = datetime(2026, 4, 21, 11, 0, 0)
        arrival_time = datetime(2026, 4, 21, 10, 0, 0)

        monitor.roosting_mode_active = False
        monitor.arrival_pending = False
        fake_metadata = {"visit_start": arrival_time, "visit_end": now, "duration_seconds": 3600}
        monitor.visit_recorder.stop_recording.return_value = ("/fake/visit.mp4", fake_metadata)

        metadata = {"visit_start": arrival_time, "visit_end": now}

        with patch("kanyo.detection.buffer_monitor.get_output_path") as mock_path:
            mock_path.return_value = MagicMock(exists=lambda: False)
            monitor._handle_event(FalconEvent.DEPARTED, now, metadata)

        monitor.visit_recorder.stop_recording.assert_called_once_with(now)
        monitor.clip_manager.create_clip_from_buffer.assert_not_called()
