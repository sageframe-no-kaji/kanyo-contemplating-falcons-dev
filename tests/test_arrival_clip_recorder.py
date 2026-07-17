"""Tests for arrival clip recorder module."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from kanyo.utils.arrival_clip_recorder import ArrivalClipRecorder
from kanyo.utils.visit_recorder import ffmpeg_log_path


def make_clip_manager() -> Mock:
    """Mock BufferClipManager with the timing attributes the recorder reads."""
    manager = Mock()
    manager.clip_arrival_before = 15
    manager.clip_arrival_after = 30
    manager.clip_fps = 30
    return manager


def make_inner_recorder(final_path=None) -> Mock:
    """Mock VisitRecorder whose stop_recording returns the given final path."""
    inner = Mock()
    inner.stop_recording.return_value = (final_path, {})
    return inner


class TestIsRecording:
    """Tests for the is_recording state check."""

    def test_not_recording_initially(self):
        """A fresh recorder is idle."""
        acr = ArrivalClipRecorder(make_clip_manager())
        assert acr.is_recording() is False

    def test_recording_when_recorder_active(self):
        """An active inner recorder means a clip is in flight."""
        acr = ArrivalClipRecorder(make_clip_manager())
        acr._recorder = Mock()
        assert acr.is_recording() is True


class TestStartRecording:
    """Tests for start_recording outcomes."""

    def test_returns_false_when_clip_creation_fails(self):
        """If the clip manager can't start ffmpeg, no recording state is kept."""
        manager = make_clip_manager()
        manager.create_standalone_arrival_clip.return_value = (None, None)
        acr = ArrivalClipRecorder(manager)

        result = acr.start_recording(
            arrival_time=datetime(2026, 1, 3, 10, 0, 0),
            lead_in_frames=[],
            frame_size=(1280, 720),
        )

        assert result is False
        assert acr.is_recording() is False

    def test_success_initializes_frame_budget(self):
        """Frame budget is duration * fps; lead-in frames count as already written."""
        manager = make_clip_manager()
        manager.create_standalone_arrival_clip.return_value = (
            Path("/fake/arrival.mp4.tmp"),
            make_inner_recorder(),
        )
        acr = ArrivalClipRecorder(manager)

        lead_in = [Mock(), Mock(), Mock()]
        result = acr.start_recording(
            arrival_time=datetime(2026, 1, 3, 10, 0, 0),
            lead_in_frames=lead_in,
            frame_size=(1280, 720),
        )

        assert result is True
        assert acr._frames_written == 3
        assert acr._max_frames == 45 * 30  # (before + after) * fps
        assert acr._max_duration_seconds == 45.0


class TestWriteFrame:
    """Tests for write_frame stop conditions."""

    def test_ignored_when_not_recording(self):
        """Frames arriving while idle are dropped without error."""
        acr = ArrivalClipRecorder(make_clip_manager())

        acr.write_frame(Mock(), datetime(2026, 1, 3, 10, 0, 1))

        assert acr._frames_written == 0
        assert acr.is_recording() is False

    def test_stops_on_frame_count_fallback(self):
        """When wall-clock hasn't elapsed, the frame-count budget still closes the clip."""
        acr = ArrivalClipRecorder(make_clip_manager())
        inner = make_inner_recorder()
        acr._recorder = inner
        acr._clip_path = Path("/fake/arrival.mp4.tmp")
        acr._max_frames = 2
        acr._max_duration_seconds = 45.0
        start = datetime(2026, 1, 3, 10, 0, 0)
        acr._start_time = start

        acr.write_frame(Mock(), start + timedelta(seconds=1))
        assert acr.is_recording() is True

        acr.write_frame(Mock(), start + timedelta(seconds=2))

        inner.stop_recording.assert_called_once()
        assert acr.is_recording() is False


class TestStopRecording:
    """Tests for stop_recording finalization."""

    def test_noop_when_not_recording(self):
        """Stopping an idle recorder does nothing."""
        acr = ArrivalClipRecorder(make_clip_manager())

        acr.stop_recording(datetime(2026, 1, 3, 10, 0, 45))  # Must not raise

        assert acr.is_recording() is False

    def test_uses_final_path_and_deletes_ffmpeg_log(self, tmp_path):
        """The renamed final path wins, and its ffmpeg log is cleaned up."""
        final_path = tmp_path / "falcon_100000_000000_arrival.mp4"
        final_path.write_text("clip")
        # The log is created against the .mp4.tmp path -> X.mp4.ffmpeg.log
        ffmpeg_log = ffmpeg_log_path(final_path)
        ffmpeg_log.write_text("ffmpeg output")

        acr = ArrivalClipRecorder(make_clip_manager())
        acr._recorder = make_inner_recorder(final_path=final_path)
        acr._clip_path = final_path.with_suffix(".mp4.tmp")
        acr._frames_written = 10

        acr.stop_recording(datetime(2026, 1, 3, 10, 0, 45))

        assert not ffmpeg_log.exists()
        assert acr.is_recording() is False
        assert acr._clip_path is None
        assert acr._frames_written == 0

    def test_survives_undeletable_ffmpeg_log(self, tmp_path):
        """A log that can't be unlinked is logged and skipped, not fatal."""
        final_path = tmp_path / "falcon_100000_000000_arrival.mp4"
        final_path.write_text("clip")
        # A directory at the log path makes unlink() raise
        ffmpeg_log = ffmpeg_log_path(final_path)
        ffmpeg_log.mkdir()

        acr = ArrivalClipRecorder(make_clip_manager())
        acr._recorder = make_inner_recorder(final_path=final_path)
        acr._clip_path = final_path.with_suffix(".mp4.tmp")

        acr.stop_recording(datetime(2026, 1, 3, 10, 0, 45))  # Must not raise

        assert ffmpeg_log.exists()
        assert acr.is_recording() is False

    def test_falls_back_to_tmp_path_when_no_final_path(self, tmp_path):
        """Without a rename, cleanup targets the .tmp clip path."""
        clip_path = tmp_path / "falcon_100000_000000_arrival.mp4.tmp"
        clip_path.write_text("clip")
        ffmpeg_log = ffmpeg_log_path(clip_path)
        ffmpeg_log.write_text("ffmpeg output")

        acr = ArrivalClipRecorder(make_clip_manager())
        acr._recorder = make_inner_recorder(final_path=None)
        acr._clip_path = clip_path

        acr.stop_recording(datetime(2026, 1, 3, 10, 0, 45))

        assert not ffmpeg_log.exists()
        assert acr.is_recording() is False


class TestRenameToFinal:
    """Tests for rename_to_final delegation."""

    def test_delegates_to_inner_recorder(self):
        """An active recording delegates the rename to the visit recorder."""
        acr = ArrivalClipRecorder(make_clip_manager())
        inner = Mock()
        inner.rename_to_final.return_value = Path("/fake/arrival.mp4")
        acr._recorder = inner

        assert acr.rename_to_final() == Path("/fake/arrival.mp4")
        inner.rename_to_final.assert_called_once()

    def test_returns_none_when_idle(self):
        """No active recording means nothing to rename."""
        acr = ArrivalClipRecorder(make_clip_manager())
        assert acr.rename_to_final() is None


class TestGetTempPath:
    """Tests for get_temp_path delegation."""

    def test_delegates_to_inner_recorder(self):
        """An active recording exposes the inner recorder's temp path."""
        acr = ArrivalClipRecorder(make_clip_manager())
        inner = Mock()
        inner.get_temp_path.return_value = Path("/fake/arrival.mp4.tmp")
        acr._recorder = inner

        assert acr.get_temp_path() == Path("/fake/arrival.mp4.tmp")
        inner.get_temp_path.assert_called_once()

    def test_returns_none_when_idle(self):
        """No active recording means no temp file."""
        acr = ArrivalClipRecorder(make_clip_manager())
        assert acr.get_temp_path() is None
