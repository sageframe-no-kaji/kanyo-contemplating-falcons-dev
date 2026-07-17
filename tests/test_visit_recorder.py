"""Tests for visit recorder module."""

import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from kanyo.utils.visit_recorder import VisitRecorder, ffmpeg_log_path


def _make_recording_recorder(
    tmp_path: Path,
    fps: int = 30,
    stream_recovery_threshold: int = 30,
) -> tuple[VisitRecorder, MagicMock]:
    """Build a recorder in a simulated recording state (no ffmpeg launched)."""
    with patch(
        "kanyo.utils.visit_recorder.detect_hardware_encoder",
        return_value="libx264",
    ):
        recorder = VisitRecorder(
            clips_dir=str(tmp_path),
            fps=fps,
            stream_recovery_threshold=stream_recovery_threshold,
        )
    mock_process = MagicMock()
    mock_process.poll.return_value = None  # Still running
    mock_process.stdin = MagicMock()
    mock_process.stdin.fileno.return_value = 3
    recorder._process = mock_process
    return recorder, mock_process


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


class TestStreamOutageHandling:
    """Tests for None-frame (stream outage) handling in write_frame."""

    def test_stream_outage_exceeded_false_initially(self, tmp_path):
        """Fresh recorder reports no outage."""
        recorder, _ = _make_recording_recorder(tmp_path)
        assert recorder.stream_outage_exceeded is False

    def test_none_frame_without_freeze_frame_is_skipped(self, tmp_path):
        """A None frame before any good frame is a no-op, not a failure."""
        recorder, mock_process = _make_recording_recorder(tmp_path)

        result = recorder.write_frame(None)

        assert result is True
        assert recorder._consecutive_none_frames == 1
        mock_process.stdin.write.assert_not_called()

    @patch("select.select", return_value=([], [3], []))
    def test_none_frame_uses_freeze_frame(self, mock_select, tmp_path):
        """During an outage the last good frame is written as a freeze frame."""
        recorder, mock_process = _make_recording_recorder(tmp_path)
        good_frame = np.full((4, 4, 3), 42, dtype=np.uint8)

        assert recorder.write_frame(good_frame) is True
        assert recorder.write_frame(None) is True

        # Two writes: the good frame, then the freeze frame with the same bytes
        assert mock_process.stdin.write.call_count == 2
        first_bytes = mock_process.stdin.write.call_args_list[0][0][0]
        second_bytes = mock_process.stdin.write.call_args_list[1][0][0]
        assert first_bytes == second_bytes
        assert recorder._frame_count == 2

    def test_outage_exceeding_threshold_returns_false(self, tmp_path):
        """An outage longer than the recovery threshold fails the write."""
        recorder, _ = _make_recording_recorder(tmp_path, fps=1, stream_recovery_threshold=2)
        # Threshold is 2s @ 1fps = 2 frames; third None frame exceeds it
        assert recorder.write_frame(None) is True
        assert recorder.write_frame(None) is True
        assert recorder.write_frame(None) is False
        assert recorder.stream_outage_exceeded is True

    @patch("select.select", return_value=([], [3], []))
    def test_stream_recovery_resets_outage_counter(self, mock_select, tmp_path):
        """A good frame after an outage resets the None-frame counter."""
        recorder, _ = _make_recording_recorder(tmp_path)
        good_frame = np.zeros((4, 4, 3), dtype=np.uint8)

        recorder.write_frame(good_frame)
        recorder.write_frame(None)
        assert recorder._consecutive_none_frames == 1

        recorder.write_frame(good_frame)
        assert recorder._consecutive_none_frames == 0
        assert recorder.stream_outage_exceeded is False

    @patch("select.select", return_value=([], [3], []))
    def test_write_frame_infers_frame_size_when_unset(self, mock_select, tmp_path):
        """write_frame derives frame size from the first frame if unknown."""
        recorder, _ = _make_recording_recorder(tmp_path)
        recorder._frame_size = None

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        assert recorder.write_frame(frame) is True
        assert recorder._frame_size == (640, 480)


class TestWriteRawFrameFailures:
    """Tests for _write_raw_frame error paths."""

    def test_no_process_returns_false(self, tmp_path):
        """Without an ffmpeg process there is nothing to write to."""
        recorder, _ = _make_recording_recorder(tmp_path)
        recorder._process = None

        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        assert recorder._write_raw_frame(frame) is False

    def test_no_stdin_returns_false(self, tmp_path):
        """A process without stdin cannot accept frames."""
        recorder, mock_process = _make_recording_recorder(tmp_path)
        mock_process.stdin = None

        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        assert recorder._write_raw_frame(frame) is False

    @patch("select.select", return_value=([], [], []))
    def test_stdin_not_ready_drops_frame(self, mock_select, tmp_path):
        """A stalled encoder (stdin not writable) drops the frame."""
        recorder, mock_process = _make_recording_recorder(tmp_path)

        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        assert recorder.write_frame(frame) is False
        mock_process.stdin.write.assert_not_called()
        assert recorder._frame_count == 0

    @patch("select.select", return_value=([], [3], []))
    def test_broken_pipe_returns_false(self, mock_select, tmp_path):
        """A broken pipe (ffmpeg died) fails the write without raising."""
        recorder, mock_process = _make_recording_recorder(tmp_path)
        mock_process.stdin.write.side_effect = BrokenPipeError("pipe closed")

        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        assert recorder.write_frame(frame) is False
        assert recorder._frame_count == 0

    @patch("select.select", return_value=([], [3], []))
    def test_closed_stdin_value_error_returns_false(self, mock_select, tmp_path):
        """A closed stdin (ValueError) fails the write without raising."""
        recorder, mock_process = _make_recording_recorder(tmp_path)
        mock_process.stdin.write.side_effect = ValueError("I/O operation on closed file")

        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        assert recorder.write_frame(frame) is False


class TestStartRecordingEncoderBranches:
    """Tests for encoder-specific ffmpeg command construction."""

    @pytest.mark.parametrize(
        "encoder,expected_args",
        [
            ("h264_videotoolbox", ["h264_videotoolbox", "-q:v"]),
            ("h264_vaapi", ["h264_vaapi", "-vaapi_device", "format=nv12,hwupload"]),
            ("h264_nvenc", ["h264_nvenc", "-cq"]),
            ("libx264", ["libx264", "-crf", "-preset"]),
        ],
    )
    @patch("subprocess.Popen")
    def test_encoder_command_options(self, mock_popen, encoder, expected_args, tmp_path):
        """Each detected encoder produces its specific ffmpeg options."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value=encoder,
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))

        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))

        cmd = mock_popen.call_args[0][0]
        for arg in expected_args:
            assert arg in cmd

    @patch("subprocess.Popen")
    def test_popen_failure_cleans_up_and_raises(self, mock_popen, tmp_path):
        """A failure launching ffmpeg propagates after cleaning up state."""
        mock_popen.side_effect = OSError("ffmpeg not found")

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))

        with pytest.raises(OSError):
            recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))

        assert recorder._process is None
        assert recorder._stderr_file is None
        assert recorder.is_recording is False

    @patch("select.select", return_value=([], [3], []))
    @patch("subprocess.Popen")
    def test_lead_in_frames_written_on_start(self, mock_popen, mock_select, tmp_path):
        """Buffered lead-in frames are decoded and written at recording start."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.stdin.fileno.return_value = 3
        mock_popen.return_value = mock_process

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))

        lead_in = []
        for _ in range(3):
            buffered = MagicMock()
            buffered.decode.return_value = np.zeros((4, 4, 3), dtype=np.uint8)
            lead_in.append(buffered)

        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0), lead_in_frames=lead_in)

        assert mock_process.stdin.write.call_count == 3
        assert recorder._frame_count == 3
        # Arrival event offset reflects the lead-in frames already written
        assert recorder._events[0]["type"] == "arrival"
        assert recorder._events[0]["offset_seconds"] == 3 / 30


class TestStopRecordingEdgeCases:
    """Tests for stop_recording error and cleanup paths."""

    def test_stop_when_not_recording_returns_empty(self, tmp_path):
        """Stopping an idle recorder is a safe no-op."""
        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))

        path, metadata = recorder.stop_recording(datetime.now())

        assert path is None
        assert metadata == {}

    @patch("subprocess.Popen")
    def test_stop_kills_ffmpeg_on_timeout(self, mock_popen, tmp_path):
        """An ffmpeg that won't finish is killed and cleanup still happens."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30),
            0,
        ]
        mock_popen.return_value = mock_process

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))

        path, metadata = recorder.stop_recording(datetime(2024, 1, 15, 15, 0, 0))

        mock_process.kill.assert_called_once()
        assert mock_process.wait.call_count == 2
        assert recorder._stderr_file is None
        assert path is not None
        assert "events" in metadata

    @patch("subprocess.Popen")
    def test_stop_survives_close_error(self, mock_popen, tmp_path):
        """An error while closing ffmpeg still produces metadata and cleanup."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.stdin.close.side_effect = RuntimeError("close failed")
        mock_popen.return_value = mock_process

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))

        path, metadata = recorder.stop_recording(datetime(2024, 1, 15, 15, 0, 0))

        assert recorder._stderr_file is None
        assert path is not None
        assert metadata["duration_seconds"] == 1800.0

    @patch("subprocess.Popen")
    def test_confirmed_stop_survives_rename_failure(self, mock_popen, tmp_path):
        """A failed .tmp → .mp4 rename is logged; the .tmp path is returned."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))
        tmp_file = recorder._visit_path
        assert tmp_file is not None
        tmp_file.write_bytes(b"fake mp4 data")
        recorder.rename_to_final()  # confirm while recording

        with patch.object(Path, "rename", side_effect=OSError("permission denied")):
            path, metadata = recorder.stop_recording(datetime(2024, 1, 15, 15, 0, 0))

        # Rename failed, so the .tmp file survives and metadata points at it
        assert path == tmp_file
        assert metadata["visit_file"].endswith(".tmp")
        assert tmp_file.exists()

    @patch("subprocess.Popen")
    def test_confirmed_stop_survives_log_unlink_failure(self, mock_popen, tmp_path):
        """A failed ffmpeg-log deletion does not break stop_recording."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))
        tmp_file = recorder._visit_path
        final_file = recorder._final_path
        assert tmp_file is not None and final_file is not None
        tmp_file.write_bytes(b"fake mp4 data")
        # start_recording created the log at the real name; the deletion
        # branch must run against that file
        assert ffmpeg_log_path(final_file).exists()
        recorder.rename_to_final()

        with patch.object(Path, "unlink", side_effect=OSError("busy")):
            path, metadata = recorder.stop_recording(datetime(2024, 1, 15, 15, 0, 0))

        assert path == final_file
        assert metadata["visit_file"] == str(final_file)
        assert ffmpeg_log_path(final_file).exists()  # unlink failed, log kept


class TestFfmpegLogPath:
    """Tests for the shared ffmpeg log naming convention."""

    def test_tmp_path_maps_to_final_log_name(self):
        """A .mp4.tmp working path yields the log beside the final .mp4."""
        log = ffmpeg_log_path(Path("/clips/2026-01-03/falcon_arrival.mp4.tmp"))
        assert log == Path("/clips/2026-01-03/falcon_arrival.mp4.ffmpeg.log")

    def test_final_path_maps_to_same_log_name(self):
        """The renamed final .mp4 path yields the identical log name."""
        log = ffmpeg_log_path(Path("/clips/2026-01-03/falcon_arrival.mp4"))
        assert log == Path("/clips/2026-01-03/falcon_arrival.mp4.ffmpeg.log")


class TestConfirmedStopLogCleanup:
    """Regression tests (027): success-path cleanup targets the real log."""

    @patch("subprocess.Popen")
    def test_confirmed_stop_deletes_the_log_it_created(self, mock_popen, tmp_path):
        """The log created at start (X.mp4.ffmpeg.log) is gone after a
        confirmed stop.

        Before 027 the cleanup computed X.ffmpeg.log from the renamed
        final path — a name that never exists — so logs accumulated on
        every successful recording.
        """
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))
        tmp_file = recorder._visit_path
        final_file = recorder._final_path
        assert tmp_file is not None and final_file is not None
        log_file = ffmpeg_log_path(tmp_file)
        assert log_file.exists()  # start_recording really opened it
        assert log_file == ffmpeg_log_path(final_file)  # same name both sides
        tmp_file.write_bytes(b"fake mp4 data")
        recorder.rename_to_final()  # confirm while recording

        path, _ = recorder.stop_recording(datetime(2024, 1, 15, 15, 0, 0))

        assert path == final_file
        assert final_file.exists()
        assert not log_file.exists()

    @patch("subprocess.Popen")
    def test_unconfirmed_stop_keeps_the_log(self, mock_popen, tmp_path):
        """An unconfirmed (cancelled) recording keeps its log for debugging."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))
        tmp_file = recorder._visit_path
        assert tmp_file is not None
        log_file = ffmpeg_log_path(tmp_file)

        recorder.stop_recording(datetime(2024, 1, 15, 15, 0, 0))

        assert log_file.exists()


class TestExtractClipDelegation:
    """Tests for the instance extract_clip delegating to the static method."""

    @patch("subprocess.run")
    def test_extract_clip_delegates_when_visit_file_exists(self, mock_run, tmp_path):
        """With a visit file on disk, extraction runs against it."""
        mock_run.return_value = MagicMock(returncode=0)

        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        visit_file = tmp_path / "visit.mp4"
        visit_file.write_bytes(b"fake mp4 data")
        recorder._visit_path = visit_file

        result = recorder.extract_clip(
            start_offset=2.0,
            duration=4.0,
            output_path=tmp_path / "clip.mp4",
        )

        assert result is True
        cmd = mock_run.call_args[0][0]
        assert str(visit_file) in cmd


class TestExtractClipFromFileErrorPaths:
    """Tests for extract_clip_from_file error handling."""

    @patch("subprocess.run")
    def test_log_unlink_failure_still_succeeds(self, mock_run, tmp_path):
        """A leftover ffmpeg log that can't be deleted doesn't fail extraction."""
        mock_run.return_value = MagicMock(returncode=0)

        visit_file = tmp_path / "visit.mp4"
        visit_file.write_bytes(b"fake mp4 data")
        output_path = tmp_path / "clip.mp4"

        with patch.object(Path, "unlink", side_effect=OSError("busy")):
            result = VisitRecorder.extract_clip_from_file(
                visit_file,
                start_offset=1.0,
                duration=2.0,
                output_path=output_path,
            )

        assert result is True

    @patch("subprocess.run")
    def test_subprocess_exception_returns_false(self, mock_run, tmp_path):
        """A hung ffmpeg (TimeoutExpired) is caught and reported as failure."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60)

        visit_file = tmp_path / "visit.mp4"
        visit_file.write_bytes(b"fake mp4 data")

        result = VisitRecorder.extract_clip_from_file(
            visit_file,
            start_offset=1.0,
            duration=2.0,
            output_path=tmp_path / "clip.mp4",
        )

        assert result is False


class TestRenameToFinalImmediate:
    """Tests for rename_to_final when not recording (immediate rename)."""

    def test_immediate_rename_success(self, tmp_path):
        """When idle, rename_to_final renames .tmp to .mp4 on the spot."""
        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        tmp_file = tmp_path / "visit.mp4.tmp"
        tmp_file.write_bytes(b"fake mp4 data")
        final_file = tmp_path / "visit.mp4"
        recorder._visit_path = tmp_file
        recorder._final_path = final_file

        result = recorder.rename_to_final()

        assert result == final_file
        assert final_file.exists()
        assert not tmp_file.exists()
        assert recorder._visit_path == final_file


class TestGetTempPath:
    """Tests for get_temp_path."""

    def test_returns_tmp_path_while_unconfirmed(self, tmp_path):
        """A .tmp visit path is exposed for deletion of cancelled recordings."""
        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        assert recorder.get_temp_path() is None  # no visit path yet

        recorder._visit_path = tmp_path / "visit.mp4.tmp"
        assert recorder.get_temp_path() == recorder._visit_path

        recorder._visit_path = tmp_path / "visit.mp4"  # finalized
        assert recorder.get_temp_path() is None


class TestRenameToFinalErrorPath:
    """Tests for rename_to_final immediate-rename failure."""

    def test_immediate_rename_failure_returns_none(self, tmp_path):
        """A failed immediate rename returns None and leaves the .tmp path."""
        with patch(
            "kanyo.utils.visit_recorder.detect_hardware_encoder",
            return_value="libx264",
        ):
            recorder = VisitRecorder(clips_dir=str(tmp_path))
        tmp_file = tmp_path / "visit.mp4.tmp"
        tmp_file.write_bytes(b"fake mp4 data")
        recorder._visit_path = tmp_file
        recorder._final_path = tmp_path / "visit.mp4"

        with patch.object(Path, "rename", side_effect=OSError("permission denied")):
            result = recorder.rename_to_final()

        assert result is None
        assert recorder._visit_path == tmp_file
