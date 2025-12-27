"""Tests for visit recorder module."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from kanyo.utils.visit_recorder import VisitRecorder


class TestVisitRecorderInit:
    """Tests for VisitRecorder initialization."""

    def test_default_init(self):
        """Test default initialization."""
        recorder = VisitRecorder()
        assert recorder.clips_dir == Path("clips")
        assert recorder.fps == 30
        assert recorder.crf == 23
        assert recorder.lead_in_seconds == 15
        assert recorder.lead_out_seconds == 15

    def test_custom_init(self):
        """Test custom initialization."""
        recorder = VisitRecorder(
            clips_dir="output/visits",
            fps=24,
            crf=18,
            lead_in_seconds=20,
            lead_out_seconds=10,
        )
        assert recorder.clips_dir == Path("output/visits")
        assert recorder.fps == 24
        assert recorder.crf == 18
        assert recorder.lead_in_seconds == 20
        assert recorder.lead_out_seconds == 10


class TestVisitRecorderProperties:
    """Tests for VisitRecorder properties."""

    def test_is_recording_false_initially(self):
        """Test that is_recording is False when not recording."""
        recorder = VisitRecorder()
        assert recorder.is_recording is False

    def test_current_visit_path_none_initially(self):
        """Test that current_visit_path is None when not recording."""
        recorder = VisitRecorder()
        assert recorder.current_visit_path is None

    def test_current_offset_seconds_zero_initially(self):
        """Test that offset is 0 when not recording."""
        recorder = VisitRecorder()
        assert recorder.current_offset_seconds == 0


class TestVisitRecorderLogEvent:
    """Tests for event logging."""

    def test_log_event_appends_to_list(self):
        """Test that log_event adds event with offset."""
        recorder = VisitRecorder()
        recorder._frame_count = 300  # 10 seconds at 30fps

        timestamp = datetime.now()
        recorder.log_event("roosting", timestamp, {"extra": "data"})

        assert len(recorder._events) == 1
        event = recorder._events[0]
        assert event["type"] == "roosting"
        assert event["offset_seconds"] == 10.0
        assert event["timestamp"] == timestamp.isoformat()
        assert event["extra"] == "data"

    def test_log_event_multiple(self):
        """Test logging multiple events."""
        recorder = VisitRecorder()

        recorder._frame_count = 0
        recorder.log_event("arrival", datetime.now())

        recorder._frame_count = 150  # 5 seconds
        recorder.log_event("roosting", datetime.now())

        recorder._frame_count = 600  # 20 seconds
        recorder.log_event("departure", datetime.now())

        assert len(recorder._events) == 3
        assert recorder._events[0]["type"] == "arrival"
        assert recorder._events[1]["type"] == "roosting"
        assert recorder._events[2]["type"] == "departure"


class TestExtractClipFromFile:
    """Tests for static clip extraction method."""

    @patch("subprocess.run")
    def test_extract_clip_success(self, mock_run, tmp_path):
        """Test successful clip extraction."""
        mock_run.return_value = MagicMock(returncode=0)

        visit_file = tmp_path / "visit.mp4"
        visit_file.touch()
        output_path = tmp_path / "clip.mp4"

        result = VisitRecorder.extract_clip_from_file(
            visit_file,
            start_offset=10.0,
            duration=5.0,
            output_path=output_path,
        )

        assert result is True
        mock_run.assert_called_once()

        # Verify ffmpeg command includes correct arguments
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "ffmpeg" in cmd[0]
        assert "-ss" in cmd
        assert "10.0" in cmd
        assert "-t" in cmd
        assert "5.0" in cmd

    @patch("subprocess.run")
    def test_extract_clip_failure(self, mock_run, tmp_path):
        """Test failed clip extraction."""
        mock_run.return_value = MagicMock(returncode=1, stderr="Error")

        visit_file = tmp_path / "visit.mp4"
        visit_file.touch()
        output_path = tmp_path / "clip.mp4"

        result = VisitRecorder.extract_clip_from_file(
            visit_file,
            start_offset=10.0,
            duration=5.0,
            output_path=output_path,
        )

        assert result is False

    def test_extract_clip_missing_file(self, tmp_path):
        """Test extraction from non-existent file returns False."""
        # Note: This tests the instance method which checks for file existence
        recorder = VisitRecorder()
        recorder._visit_path = tmp_path / "nonexistent.mp4"

        result = recorder.extract_clip(
            start_offset=0,
            duration=5,
            output_path=tmp_path / "clip.mp4",
        )

        assert result is False


class TestVisitRecorderStartStop:
    """Tests for start/stop recording (mocked subprocess)."""

    @patch("subprocess.Popen")
    def test_start_recording_creates_path(self, mock_popen, tmp_path):
        """Test that start_recording creates output path."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Still running
        mock_process.stdin = MagicMock()
        mock_popen.return_value = mock_process

        recorder = VisitRecorder(clips_dir=str(tmp_path))
        arrival_time = datetime(2024, 1, 15, 14, 30, 0)

        result = recorder.start_recording(arrival_time, frame_size=(1920, 1080))

        assert result is not None
        assert "visit" in str(result)
        assert recorder.is_recording is True

    @patch("subprocess.Popen")
    def test_stop_recording_returns_metadata(self, mock_popen, tmp_path):
        """Test that stop_recording returns metadata."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        recorder = VisitRecorder(clips_dir=str(tmp_path))
        arrival_time = datetime(2024, 1, 15, 14, 30, 0)
        departure_time = datetime(2024, 1, 15, 15, 0, 0)

        recorder.start_recording(arrival_time)
        recorder._frame_count = 54000  # 30 minutes at 30fps

        path, metadata = recorder.stop_recording(departure_time)

        assert metadata["visit_start"] == arrival_time.isoformat()
        assert metadata["visit_end"] == departure_time.isoformat()
        assert metadata["duration_seconds"] == 1800.0  # 30 minutes
        assert "events" in metadata
        # Should have arrival and departure events
        assert len(metadata["events"]) == 2

    @patch("subprocess.Popen")
    def test_double_start_stops_previous(self, mock_popen, tmp_path):
        """Test that starting a new recording stops the previous one."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        recorder = VisitRecorder(clips_dir=str(tmp_path))

        recorder.start_recording(datetime(2024, 1, 15, 14, 0, 0))
        first_path = recorder.current_visit_path

        # Start a second recording
        recorder.start_recording(datetime(2024, 1, 15, 15, 0, 0))
        second_path = recorder.current_visit_path

        # Paths should be different
        assert first_path != second_path


class TestVisitRecorderWriteFrame:
    """Tests for frame writing (mocked)."""

    @patch("subprocess.Popen")
    def test_write_frame_when_recording(self, mock_popen, tmp_path):
        """Test writing frame when recording."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_popen.return_value = mock_process

        recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime.now())

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = recorder.write_frame(frame)

        assert result is True
        assert recorder._frame_count == 1
        mock_process.stdin.write.assert_called_once()

    def test_write_frame_when_not_recording(self):
        """Test that write_frame returns False when not recording."""
        recorder = VisitRecorder()
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        result = recorder.write_frame(frame)

        assert result is False
