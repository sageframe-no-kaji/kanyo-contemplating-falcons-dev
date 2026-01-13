"""Tests for visit recorder module."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

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
    @patch("select.select")
    def test_write_frame_when_recording(self, mock_select, mock_popen, tmp_path):
        """Test writing frame when recording."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.stdin.fileno.return_value = 3  # Mock file descriptor as integer
        mock_popen.return_value = mock_process

        # Mock select to indicate stdin is ready for writing
        mock_select.return_value = ([], [3], [])

        recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime.now())

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = recorder.write_frame(frame)

        assert result is True
        assert recorder._frame_count == 1
        mock_process.stdin.write.assert_called_once()
        mock_select.assert_called_once()

    def test_write_frame_when_not_recording(self):
        """Test that write_frame returns False when not recording."""
        recorder = VisitRecorder()
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        result = recorder.write_frame(frame)

        assert result is False


class TestVisitRecorderDetectionTracking:
    """Tests for frame-based detection tracking."""

    def test_mark_detection_updates_last_detection_frame(self):
        """Test that mark_detection updates the last detection frame."""
        recorder = VisitRecorder()
        recorder._frame_count = 100
        # Simulate recording state
        recorder._process = MagicMock()
        recorder._process.poll.return_value = None

        recorder.mark_detection()

        assert recorder._last_detection_frame == 100

    def test_mark_detection_does_nothing_when_not_recording(self):
        """Test that mark_detection is a no-op when not recording."""
        recorder = VisitRecorder()
        recorder._frame_count = 100
        recorder._last_detection_frame = 50

        recorder.mark_detection()

        # Should not update since not recording
        assert recorder._last_detection_frame == 50

    def test_last_detection_offset_seconds(self):
        """Test that last_detection_offset_seconds calculates correctly."""
        recorder = VisitRecorder(fps=30)
        recorder._last_detection_frame = 900  # 30 seconds at 30fps

        assert recorder.last_detection_offset_seconds == 30.0

    def test_last_detection_offset_seconds_zero_initially(self):
        """Test that last_detection_offset_seconds is 0 initially."""
        recorder = VisitRecorder()
        assert recorder.last_detection_offset_seconds == 0

    @patch("subprocess.Popen")
    @patch("select.select")
    def test_mark_detection_tracks_multiple_detections(self, mock_select, mock_popen, tmp_path):
        """Test that mark_detection tracks the most recent detection frame."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.stdin.fileno.return_value = 3
        mock_popen.return_value = mock_process
        mock_select.return_value = ([], [3], [])

        recorder = VisitRecorder(clips_dir=str(tmp_path), fps=30)
        recorder.start_recording(datetime.now())

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        # Write some frames without detection
        for _ in range(10):
            recorder.write_frame(frame)

        # First detection at frame 10
        recorder.mark_detection()
        assert recorder._last_detection_frame == 10

        # Write more frames
        for _ in range(20):
            recorder.write_frame(frame)

        # Second detection at frame 30
        recorder.mark_detection()
        assert recorder._last_detection_frame == 30

    @patch("subprocess.Popen")
    def test_stop_recording_includes_last_detection_offset(self, mock_popen, tmp_path):
        """Test that stop_recording metadata includes last_detection_offset_seconds."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        recorder = VisitRecorder(clips_dir=str(tmp_path), fps=30)

        # Manually set up recording state
        arrival_time = datetime.now()
        recorder._process = mock_process
        recorder._visit_path = tmp_path / "2026-01-13" / "falcon_092736_visit.mp4.tmp"
        recorder._final_path = tmp_path / "2026-01-13" / "falcon_092736_visit.mp4"
        recorder._visit_path.parent.mkdir(parents=True, exist_ok=True)
        recorder._visit_path.touch()
        recorder._visit_start = arrival_time
        recorder._recording_start = arrival_time
        recorder._frame_count = 900  # 30 seconds of frames
        recorder._last_detection_frame = 750  # Detection at 25 seconds

        _, metadata = recorder.stop_recording(datetime.now())

        assert "last_detection_offset_seconds" in metadata
        assert metadata["last_detection_offset_seconds"] == 25.0
        assert metadata["last_detection_frame"] == 750

    def test_start_recording_resets_last_detection_frame(self, tmp_path):
        """Test that start_recording resets last_detection_frame to 0."""
        recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder._last_detection_frame = 500

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            recorder.start_recording(datetime.now())

        assert recorder._last_detection_frame == 0
