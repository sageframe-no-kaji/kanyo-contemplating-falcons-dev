"""Regression tests for 021-E: roosting stop mode must finalize .mp4.tmp → .mp4.

Bug: in roosting-stop the visit recorder was being stopped without first being
marked confirmed, so VisitRecorder.stop_recording() left the on-disk file as
`<basename>.mp4.tmp` (no rename) and metadata['visit_file'] pointed at the
.tmp path. Fix: call visit_recorder.rename_to_final() before stop_recording()
in the roosting-stop branch of buffer_monitor._handle_event.
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from kanyo.utils.visit_recorder import VisitRecorder


class TestRenameToFinalIdempotent:
    """rename_to_final() must be safe to call twice."""

    def test_rename_to_final_idempotent_when_not_recording(self, tmp_path):
        recorder = VisitRecorder(clips_dir=str(tmp_path))
        # First call: nothing recording, no path set → no-op
        assert recorder.rename_to_final() is None
        # Second call: same
        assert recorder.rename_to_final() is None

    def test_rename_to_final_sets_confirmed_flag_when_recording(self, tmp_path):
        recorder = VisitRecorder(clips_dir=str(tmp_path))
        # Simulate active recording state without invoking ffmpeg
        recorder._visit_path = tmp_path / "visit.mp4.tmp"
        recorder._final_path = tmp_path / "visit.mp4"
        recorder._process = MagicMock()
        recorder._process.poll.return_value = None  # still running

        assert recorder.is_recording is True
        assert recorder._confirmed is False

        result = recorder.rename_to_final()
        assert result is None  # rename deferred to stop_recording
        assert recorder._confirmed is True

        # Calling again is harmless
        recorder.rename_to_final()
        assert recorder._confirmed is True


class TestStopRecordingFinalizesWhenConfirmed:
    """When _confirmed is set before stop_recording, the file gets renamed
    and metadata['visit_file'] points at the final .mp4 (not .mp4.tmp).
    This pins the contract that 021-E relies on."""

    @patch("subprocess.Popen")
    def test_confirmed_stop_renames_and_updates_metadata(self, mock_popen, tmp_path):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        recorder = VisitRecorder(clips_dir=str(tmp_path))
        arrival_time = datetime(2024, 1, 15, 14, 30, 0)
        departure_time = datetime(2024, 1, 15, 15, 0, 0)

        recorder.start_recording(arrival_time)
        # Create a fake .mp4.tmp file on disk (real ffmpeg would have written it)
        tmp_file = recorder._visit_path
        assert tmp_file is not None
        tmp_file.write_bytes(b"fake mp4 data")
        assert tmp_file.suffix == ".tmp"

        # This is the 021-E sequence: mark confirmed THEN stop
        recorder.rename_to_final()
        path, metadata = recorder.stop_recording(departure_time)

        # On-disk file is now .mp4 (no .tmp suffix)
        final_file = recorder._final_path
        assert final_file is not None
        assert final_file.exists()
        assert final_file.suffix == ".mp4"
        assert not tmp_file.exists()  # the .tmp was renamed

        # Metadata's visit_file points at the final .mp4, NOT the .tmp
        assert metadata["visit_file"] == str(final_file)
        assert not metadata["visit_file"].endswith(".tmp")

    @patch("subprocess.Popen")
    def test_unconfirmed_stop_leaves_tmp_intact_regression_baseline(self, mock_popen, tmp_path):
        """Negative test: skipping rename_to_final leaves .mp4.tmp on disk.

        This documents the bug that 021-E fixes. If a future change makes
        stop_recording() rename unconditionally, this test should be updated
        — but the buffer_monitor caller must still invoke rename_to_final()
        explicitly because cancelled recordings depend on the current behavior.
        """
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        recorder = VisitRecorder(clips_dir=str(tmp_path))
        recorder.start_recording(datetime(2024, 1, 15, 14, 30, 0))
        tmp_file = recorder._visit_path
        assert tmp_file is not None
        tmp_file.write_bytes(b"fake mp4 data")

        # Skip rename_to_final() — old buggy roosting-stop sequence
        _, metadata = recorder.stop_recording(datetime(2024, 1, 15, 15, 0, 0))

        # .tmp survives, .mp4 does not exist
        assert tmp_file.exists()
        assert tmp_file.suffix == ".tmp"
        assert metadata["visit_file"].endswith(".tmp")


class TestBufferMonitorRoostingStopCallsRenameToFinal:
    """Source-level check that buffer_monitor invokes rename_to_final()
    before stop_recording() in the roosting-stop branch. A pure-runtime
    test would require driving the full state machine through ROOSTING,
    which is out of scope for this regression."""

    def test_roosting_stop_branch_calls_rename_to_final(self):
        src = Path(__file__).parent.parent / "src" / "kanyo" / "detection" / "buffer_monitor.py"
        text = src.read_text()
        # The ROOSTING event-handler branch (NOT the periodic poll branch on the
        # detection side) is the one that finalizes the recording. Anchor on
        # FalconEvent.ROOSTING then scan the following block for the fix.
        idx_roosting_event = text.find("elif event_type == FalconEvent.ROOSTING:")
        assert idx_roosting_event != -1, "could not locate ROOSTING event handler"
        window = text[idx_roosting_event : idx_roosting_event + 1200]
        idx_rename = window.find("visit_recorder.rename_to_final()")
        idx_stop = window.find("visit_recorder.stop_recording(")
        assert idx_rename != -1, (
            "ROOSTING event handler (stop mode) must call "
            "self.visit_recorder.rename_to_final() — see 021-E"
        )
        assert (
            idx_stop != -1
        ), "self.visit_recorder.stop_recording() call missing in ROOSTING handler"
        assert (
            idx_rename < idx_stop
        ), "rename_to_final() must be called BEFORE stop_recording() — see 021-E"
