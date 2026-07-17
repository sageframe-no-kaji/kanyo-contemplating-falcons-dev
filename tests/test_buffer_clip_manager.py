"""Tests for buffer clip manager module."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from kanyo.detection.buffer_clip_manager import BufferClipManager
from kanyo.utils.visit_recorder import VisitRecorder, ffmpeg_log_path


def make_manager(tmp_path, **kwargs):
    """Create a manager with mocked dependencies and a mocked executor.

    The real ThreadPoolExecutor is shut down and replaced with a MagicMock so
    tests can assert on scheduled work without spawning threads.
    """
    manager = BufferClipManager(
        frame_buffer=MagicMock(),
        visit_recorder=MagicMock(),
        full_config={"timezone": "UTC"},
        clips_dir=str(tmp_path / "clips"),
        **kwargs,
    )
    manager._executor.shutdown(wait=True)
    manager._executor = MagicMock()
    return manager


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

    def test_shutdown_is_idempotent(self):
        """A second shutdown call returns early without touching the executor again."""
        mock_buffer = MagicMock()
        mock_recorder = MagicMock()

        manager = BufferClipManager(
            frame_buffer=mock_buffer,
            visit_recorder=mock_recorder,
            full_config={"timezone": "UTC"},
        )

        manager.shutdown()
        executor_spy = MagicMock()
        manager._executor = executor_spy

        manager.shutdown()  # Second call must be a no-op

        executor_spy.shutdown.assert_not_called()
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


class TestCreateArrivalClip:
    """Tests for create_arrival_clip scheduling from a visit recording."""

    def test_no_visit_file_returns_false(self, tmp_path):
        """Missing visit_file in metadata means no clip can be scheduled."""
        manager = make_manager(tmp_path)

        assert manager.create_arrival_clip({}) is False
        manager._executor.submit.assert_not_called()

    def test_nonexistent_visit_file_returns_false(self, tmp_path):
        """A visit_file path that doesn't exist on disk is rejected."""
        manager = make_manager(tmp_path)

        metadata = {"visit_file": str(tmp_path / "missing.mp4")}
        assert manager.create_arrival_clip(metadata) is False
        manager._executor.submit.assert_not_called()

    def test_missing_visit_start_returns_false(self, tmp_path):
        """Without visit_start there is no timestamp to name the clip after."""
        manager = make_manager(tmp_path)
        visit_file = tmp_path / "visit.mp4"
        visit_file.write_text("video")

        metadata = {"visit_file": str(visit_file)}
        assert manager.create_arrival_clip(metadata) is False
        manager._executor.submit.assert_not_called()

    def test_schedules_extraction_from_recording_start(self, tmp_path):
        """Arrival clip is the first (before + after) seconds of the recording."""
        manager = make_manager(tmp_path, clip_arrival_before=15, clip_arrival_after=30)
        visit_file = tmp_path / "visit.mp4"
        visit_file.write_text("video")

        metadata = {
            "visit_file": str(visit_file),
            "visit_start": datetime(2026, 1, 3, 10, 30, 0),
        }

        assert manager.create_arrival_clip(metadata) is True

        args = manager._executor.submit.call_args[0]
        assert args[0] == manager._extract_clip_from_visit
        assert args[1] == visit_file
        assert args[2] == 0  # Starts at beginning (includes lead-in)
        assert args[3] == 45  # before + after
        assert args[4].name.endswith("_arrival.mp4")
        assert args[5] == "arrival"

    def test_parses_iso_string_visit_start(self, tmp_path):
        """visit_start given as ISO string is parsed before naming the clip."""
        manager = make_manager(tmp_path)
        visit_file = tmp_path / "visit.mp4"
        visit_file.write_text("video")

        metadata = {
            "visit_file": str(visit_file),
            "visit_start": "2026-01-03T10:30:00",
        }

        assert manager.create_arrival_clip(metadata) is True

        clip_path = manager._executor.submit.call_args[0][4]
        assert "falcon_103000_" in clip_path.name


class TestCreateDepartureClip:
    """Tests for create_departure_clip offset calculation and scheduling."""

    @staticmethod
    def base_metadata(tmp_path) -> dict:
        visit_file = tmp_path / "visit.mp4"
        visit_file.write_text("video")
        return {
            "visit_file": str(visit_file),
            "visit_start": "2026-01-03T10:00:00",
            "visit_end": "2026-01-03T10:10:00",
            "recording_start": "2026-01-03T09:59:45",
            "recording_duration_seconds": 700.0,
        }

    def test_no_visit_file_returns_false(self, tmp_path):
        """Missing visit_file means no clip can be scheduled."""
        manager = make_manager(tmp_path)

        assert manager.create_departure_clip({}) is False
        manager._executor.submit.assert_not_called()

    def test_missing_visit_end_returns_false(self, tmp_path):
        """Without visit_end there is no departure moment to center on."""
        manager = make_manager(tmp_path)
        metadata = self.base_metadata(tmp_path)
        del metadata["visit_end"]

        assert manager.create_departure_clip(metadata) is False

    def test_missing_recording_duration_returns_false(self, tmp_path):
        """Zero recording duration means there is nothing to extract from."""
        manager = make_manager(tmp_path)
        metadata = self.base_metadata(tmp_path)
        metadata["recording_duration_seconds"] = 0

        assert manager.create_departure_clip(metadata) is False

    def test_frame_based_offset_preferred(self, tmp_path):
        """Frame-accurate last_detection_offset_seconds wins over wall clock."""
        manager = make_manager(tmp_path, clip_departure_before=30, clip_departure_after=15)
        metadata = self.base_metadata(tmp_path)
        metadata["last_detection_offset_seconds"] = 600.0

        assert manager.create_departure_clip(metadata) is True

        args = manager._executor.submit.call_args[0]
        assert args[0] == manager._extract_clip_from_visit
        assert args[2] == 570.0  # 600 - 30
        assert args[3] == 45  # 30 + 15
        assert args[4].name.endswith("_departure.mp4")
        assert args[5] == "departure"

    def test_wall_clock_fallback(self, tmp_path):
        """Without a frame offset, lead-in + visit duration locates the departure."""
        manager = make_manager(tmp_path, clip_departure_before=30, clip_departure_after=15)
        metadata = self.base_metadata(tmp_path)

        assert manager.create_departure_clip(metadata) is True

        # lead-in 15s + visit 600s = offset 615s; start = 615 - 30
        args = manager._executor.submit.call_args[0]
        assert args[2] == 585.0
        assert args[3] == 45

    def test_recording_duration_fallback(self, tmp_path):
        """With no timing data at all, offset is estimated as duration - exit timeout."""
        manager = make_manager(tmp_path, clip_departure_before=30, clip_departure_after=15)
        metadata = self.base_metadata(tmp_path)
        del metadata["recording_start"]

        assert manager.create_departure_clip(metadata) is True

        # offset = max(0, 700 - 90) = 610; start = 610 - 30
        args = manager._executor.submit.call_args[0]
        assert args[2] == 580.0

    def test_clip_trimmed_at_recording_end(self, tmp_path):
        """A clip extending past the file end is trimmed to the available footage."""
        manager = make_manager(tmp_path, clip_departure_before=30, clip_departure_after=15)
        metadata = self.base_metadata(tmp_path)
        metadata["recording_duration_seconds"] = 600.0
        metadata["last_detection_offset_seconds"] = 590.0

        assert manager.create_departure_clip(metadata) is True

        # start = 560; 560 + 45 > 600, so duration trims to 40
        args = manager._executor.submit.call_args[0]
        assert args[2] == 560.0
        assert args[3] == 40.0

    def test_too_short_trimmed_clip_skipped(self, tmp_path):
        """A trimmed clip under 5 seconds is useless and gets skipped."""
        manager = make_manager(tmp_path, clip_departure_before=30, clip_departure_after=15)
        metadata = self.base_metadata(tmp_path)
        metadata["recording_duration_seconds"] = 100.0
        metadata["last_detection_offset_seconds"] = 128.0

        # start = 98; only 2s of footage remain before file end
        assert manager.create_departure_clip(metadata) is False
        manager._executor.submit.assert_not_called()

    def test_accepts_datetime_objects(self, tmp_path):
        """Timestamps supplied as datetime objects need no ISO parsing."""
        manager = make_manager(tmp_path)
        metadata = self.base_metadata(tmp_path)
        metadata["visit_start"] = datetime(2026, 1, 3, 10, 0, 0)
        metadata["visit_end"] = datetime(2026, 1, 3, 10, 10, 0)
        metadata["recording_start"] = datetime(2026, 1, 3, 9, 59, 45)

        assert manager.create_departure_clip(metadata) is True


class TestCreateClipFromBuffer:
    """Tests for direct-from-buffer clip scheduling."""

    def test_schedules_extraction_around_event(self, tmp_path):
        """Clip spans before_seconds..after_seconds around the event time."""
        manager = make_manager(tmp_path)
        event_time = datetime(2026, 1, 3, 10, 30, 0)

        result = manager.create_clip_from_buffer(
            event_time=event_time,
            event_name="perch",
            before_seconds=10,
            after_seconds=20,
        )

        assert result is True
        args = manager._executor.submit.call_args[0]
        assert args[0] == manager._extract_clip_from_buffer
        assert args[1] == event_time - timedelta(seconds=10)
        assert args[2] == event_time + timedelta(seconds=20)
        assert args[3].name.endswith("_perch.mp4")
        assert args[4] == "perch"


class TestExtractCandidateClip:
    """Tests for the departure-candidate snapshot mechanism (022-C)."""

    def test_returns_future_for_explicit_path(self, tmp_path):
        """Caller-supplied path is used verbatim and the future is handed back."""
        manager = make_manager(tmp_path)
        start = datetime(2026, 1, 3, 10, 30, 0)
        end = datetime(2026, 1, 3, 10, 31, 0)
        clip_path = tmp_path / "candidate.mp4.tmp"

        future = manager.extract_candidate_clip(start, end, clip_path)

        assert future is manager._executor.submit.return_value
        args = manager._executor.submit.call_args[0]
        assert args[0] == manager._extract_clip_from_buffer
        assert args[1] == start
        assert args[2] == end
        assert args[3] == clip_path
        assert args[4] == "departure-candidate"


class TestExtractClipFromBuffer:
    """Tests for the buffer extraction worker (called directly, no executor)."""

    def test_success_returns_path(self, tmp_path):
        """Successful buffer extraction returns the written path string."""
        manager = make_manager(tmp_path, clip_fps=24, clip_crf=20)
        manager.frame_buffer.extract_clip.return_value = True
        start = datetime(2026, 1, 3, 10, 30, 0)
        end = datetime(2026, 1, 3, 10, 31, 0)
        clip_path = tmp_path / "clip.mp4"

        result = manager._extract_clip_from_buffer(start, end, clip_path, "arrival")

        assert result == str(clip_path)
        manager.frame_buffer.extract_clip.assert_called_once_with(
            start_time=start,
            end_time=end,
            output_path=clip_path,
            fps=24,
            crf=20,
        )

    def test_failure_returns_none(self, tmp_path):
        """A failed buffer extraction returns None."""
        manager = make_manager(tmp_path)
        manager.frame_buffer.extract_clip.return_value = False

        result = manager._extract_clip_from_buffer(
            datetime(2026, 1, 3, 10, 30, 0),
            datetime(2026, 1, 3, 10, 31, 0),
            tmp_path / "clip.mp4",
            "arrival",
        )

        assert result is None

    def test_exception_returns_none(self, tmp_path):
        """An exception inside extraction is swallowed and reported as None."""
        manager = make_manager(tmp_path)
        manager.frame_buffer.extract_clip.side_effect = RuntimeError("buffer gone")

        result = manager._extract_clip_from_buffer(
            datetime(2026, 1, 3, 10, 30, 0),
            datetime(2026, 1, 3, 10, 31, 0),
            tmp_path / "clip.mp4",
            "arrival",
        )

        assert result is None


class TestExtractClipFromVisit:
    """Tests for the visit-file extraction worker (called directly, no executor)."""

    def test_success_returns_path(self, tmp_path):
        """Successful visit-file extraction returns the written path string."""
        manager = make_manager(tmp_path)
        visit_file = tmp_path / "visit.mp4"
        clip_path = tmp_path / "clip.mp4"

        with patch.object(
            VisitRecorder, "extract_clip_from_file", return_value=True
        ) as mock_extract:
            result = manager._extract_clip_from_visit(visit_file, 10.0, 45.0, clip_path, "arrival")

        assert result == str(clip_path)
        mock_extract.assert_called_once_with(
            visit_file=visit_file,
            start_offset=10.0,
            duration=45.0,
            output_path=clip_path,
        )

    def test_failure_returns_none(self, tmp_path):
        """A failed ffmpeg extraction returns None."""
        manager = make_manager(tmp_path)

        with patch.object(VisitRecorder, "extract_clip_from_file", return_value=False):
            result = manager._extract_clip_from_visit(
                tmp_path / "visit.mp4", 10.0, 45.0, tmp_path / "clip.mp4", "departure"
            )

        assert result is None

    def test_exception_returns_none(self, tmp_path):
        """An exception during extraction is swallowed and reported as None."""
        manager = make_manager(tmp_path)

        with patch.object(
            VisitRecorder, "extract_clip_from_file", side_effect=OSError("disk full")
        ):
            result = manager._extract_clip_from_visit(
                tmp_path / "visit.mp4", 10.0, 45.0, tmp_path / "clip.mp4", "departure"
            )

        assert result is None


class TestCreateStandaloneArrivalClip:
    """Tests for standalone arrival clip recording (mocked ffmpeg)."""

    ARRIVAL = datetime(2026, 1, 3, 10, 30, 0)

    def _create(self, tmp_path, mock_popen, encoder, lead_in_frames=None):
        """Run create_standalone_arrival_clip with a forced encoder and mocked Popen."""
        manager = make_manager(tmp_path)
        with patch("kanyo.utils.visit_recorder.detect_hardware_encoder", return_value=encoder):
            clip_path, recorder = manager.create_standalone_arrival_clip(
                arrival_time=self.ARRIVAL,
                lead_in_frames=lead_in_frames or [],
                frame_size=(640, 480),
            )
        return clip_path, recorder, mock_popen

    @staticmethod
    def _close_stderr(recorder):
        """Close the real stderr log handle opened by the method."""
        if recorder is not None and recorder._stderr_file:
            recorder._stderr_file.close()
            recorder._stderr_file = None

    @patch("kanyo.detection.buffer_clip_manager.subprocess.Popen")
    def test_returns_tmp_path_and_initialized_recorder(self, mock_popen, tmp_path):
        """Success returns a .mp4.tmp path and a recorder primed with arrival state."""
        clip_path, recorder, _ = self._create(tmp_path, mock_popen, "libx264")

        assert clip_path is not None
        assert clip_path.name.endswith(".mp4.tmp")
        assert recorder._visit_path == clip_path
        assert recorder._final_path.name.endswith("_arrival.mp4")
        assert recorder._visit_start == self.ARRIVAL
        assert recorder._frame_size == (640, 480)
        assert recorder._events == [
            {
                "type": "arrival",
                "offset_seconds": 0,
                "timestamp": self.ARRIVAL.isoformat(),
            }
        ]
        assert recorder._process is mock_popen.return_value
        self._close_stderr(recorder)

    @patch("kanyo.detection.buffer_clip_manager.subprocess.Popen")
    def test_confirmed_stop_deletes_stderr_log(self, mock_popen, tmp_path):
        """Regression (027): the log created for a standalone arrival clip
        (X.mp4.ffmpeg.log, from the .tmp path) is deleted on confirmed stop."""
        mock_popen.return_value.poll.return_value = None  # recorder is "running"
        mock_popen.return_value.wait.return_value = 0
        clip_path, recorder, _ = self._create(tmp_path, mock_popen, "libx264")

        log_file = ffmpeg_log_path(clip_path)
        assert log_file.exists()  # really opened by create_standalone_arrival_clip
        assert log_file.name.endswith(".mp4.ffmpeg.log")

        clip_path.write_bytes(b"fake mp4 data")
        recorder.rename_to_final()  # confirm while recording
        expected_final = recorder._final_path

        final_path, _ = recorder.stop_recording(self.ARRIVAL + timedelta(seconds=45))

        assert final_path == expected_final
        assert expected_final is not None and expected_final.exists()
        assert not log_file.exists()

    @patch("kanyo.detection.buffer_clip_manager.subprocess.Popen")
    def test_videotoolbox_encoder_command(self, mock_popen, tmp_path):
        """VideoToolbox branch maps CRF to its quality scale."""
        clip_path, recorder, _ = self._create(tmp_path, mock_popen, "h264_videotoolbox")

        cmd = mock_popen.call_args[0][0]
        assert "h264_videotoolbox" in cmd
        # Default CRF 23 -> quality (51 - 23) * 2 = 56
        assert cmd[cmd.index("-q:v") + 1] == "56"
        assert cmd[-1] == str(clip_path)
        self._close_stderr(recorder)

    @patch("kanyo.detection.buffer_clip_manager.subprocess.Popen")
    def test_vaapi_encoder_command(self, mock_popen, tmp_path):
        """VAAPI branch adds the device and hwupload filter."""
        clip_path, recorder, _ = self._create(tmp_path, mock_popen, "h264_vaapi")

        cmd = mock_popen.call_args[0][0]
        assert "h264_vaapi" in cmd
        assert "-vaapi_device" in cmd
        assert cmd[cmd.index("-qp") + 1] == "23"
        self._close_stderr(recorder)

    @patch("kanyo.detection.buffer_clip_manager.subprocess.Popen")
    def test_nvenc_encoder_command(self, mock_popen, tmp_path):
        """NVENC branch uses constant-quality mode."""
        clip_path, recorder, _ = self._create(tmp_path, mock_popen, "h264_nvenc")

        cmd = mock_popen.call_args[0][0]
        assert "h264_nvenc" in cmd
        assert cmd[cmd.index("-cq") + 1] == "23"
        self._close_stderr(recorder)

    @patch("kanyo.detection.buffer_clip_manager.subprocess.Popen")
    def test_software_encoder_command(self, mock_popen, tmp_path):
        """libx264 fallback uses CRF directly."""
        clip_path, recorder, _ = self._create(tmp_path, mock_popen, "libx264")

        cmd = mock_popen.call_args[0][0]
        assert "libx264" in cmd
        assert cmd[cmd.index("-crf") + 1] == "23"
        self._close_stderr(recorder)

    @patch("kanyo.detection.buffer_clip_manager.subprocess.Popen")
    def test_popen_failure_returns_none_pair(self, mock_popen, tmp_path):
        """If ffmpeg can't start, the caller gets (None, None) and no recorder."""
        mock_popen.side_effect = OSError("ffmpeg not found")

        clip_path, recorder, _ = self._create(tmp_path, mock_popen, "libx264")

        assert clip_path is None
        assert recorder is None

    @patch.object(VisitRecorder, "_write_raw_frame")
    @patch("kanyo.detection.buffer_clip_manager.subprocess.Popen")
    def test_lead_in_frames_are_written(self, mock_popen, mock_write, tmp_path):
        """Buffered lead-in frames are decoded and piped to the recorder."""
        decoded = object()
        buffered_frame = Mock()
        buffered_frame.decode.return_value = decoded

        clip_path, recorder, _ = self._create(
            tmp_path, mock_popen, "libx264", lead_in_frames=[buffered_frame, buffered_frame]
        )

        assert mock_write.call_count == 2
        mock_write.assert_called_with(decoded)
        assert buffered_frame.decode.call_count == 2
        self._close_stderr(recorder)
