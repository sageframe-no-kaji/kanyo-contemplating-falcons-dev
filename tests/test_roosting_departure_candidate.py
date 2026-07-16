"""Tests for 022-C: departure-candidate clip snapshot in roosting-stop mode.

In roosting-stop mode DEPARTED fires with event_time = last_detection, which is
>= exit_timeout in the past — always outside the 60s buffer, so the old direct
buffer extraction failed silently every time. The candidate mechanism snapshots
the departure window at the first missed roosting poll (while it is still in
the buffer), finalizes it on DEPARTED, and discards it on re-confirmation.
"""

from concurrent.futures import Future
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.detection.event_types import FalconEvent
from kanyo.utils.output import get_output_path


def make_monitor(clips_dir: str = "clips"):
    """Return a roosting-stop BufferMonitor with external dependencies mocked out."""
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
            clips_dir=clips_dir,
            full_config={
                "roosting_recording_mode": "stop",
                "roosting_detection_interval": 30,
            },
        )
    monitor.visit_recorder.is_recording = False
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor.frame_buffer.add_frame.return_value = None
    monitor.state_machine.update.return_value = []
    monitor.clip_manager.clip_departure_before = 30
    monitor.clip_manager.clip_departure_after = 15
    return monitor


def detection(conf: float = 0.8):
    return SimpleNamespace(confidence=conf)


def make_frame():
    frame = MagicMock()
    frame.shape = (720, 1280, 3)
    return frame


def completed_future(result):
    future: Future = Future()
    future.set_result(result)
    return future


def run_poll(monitor, detections, now):
    """Run one roosting poll through process_frame at the given time."""
    monitor.detector.detect_birds.return_value = detections
    # Ensure the roosting interval gate lets the poll through
    monitor.last_roosting_check = now - timedelta(seconds=31)
    monitor.process_frame(make_frame(), frame_number=1, timestamp=now)


class TestCandidateSnapshot:
    def test_first_missed_poll_snapshots_candidate(self, tmp_path):
        """detect → miss: candidate scheduled at the final departure path + .tmp."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        monitor.roosting_mode_active = True
        monitor._roosting_last_poll_detected = True

        last_det = datetime(2026, 7, 1, 10, 0, 0)
        now = datetime(2026, 7, 1, 10, 0, 30)
        monitor.state_machine.last_detection = last_det
        fake_future = completed_future(None)
        monitor.clip_manager.extract_candidate_clip.return_value = fake_future

        run_poll(monitor, [], now)

        final_path = get_output_path(str(tmp_path), last_det, "departure", "mp4")
        tmp_clip = final_path.with_suffix(".mp4.tmp")
        monitor.clip_manager.extract_candidate_clip.assert_called_once_with(
            last_det - timedelta(seconds=30), now, tmp_clip
        )
        assert monitor._departure_candidate == (fake_future, tmp_clip, final_path)
        assert monitor._roosting_last_poll_detected is False

    def test_second_missed_poll_does_not_resnapshot(self, tmp_path):
        """Only the FIRST miss snapshots; consecutive misses leave it alone."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        monitor.roosting_mode_active = True
        monitor._roosting_last_poll_detected = True
        monitor.state_machine.last_detection = datetime(2026, 7, 1, 10, 0, 0)
        monitor.clip_manager.extract_candidate_clip.return_value = completed_future(None)

        run_poll(monitor, [], datetime(2026, 7, 1, 10, 0, 30))
        run_poll(monitor, [], datetime(2026, 7, 1, 10, 1, 0))

        assert monitor.clip_manager.extract_candidate_clip.call_count == 1

    def test_reconfirmation_discards_candidate(self, tmp_path):
        """miss → re-detect: candidate .tmp deleted, state cleared."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        monitor.roosting_mode_active = True
        monitor._roosting_last_poll_detected = False

        tmp_clip = tmp_path / "falcon_100000_000000_departure.mp4.tmp"
        tmp_clip.write_bytes(b"fake video")
        final_path = tmp_path / "falcon_100000_000000_departure.mp4"
        monitor._departure_candidate = (
            completed_future(str(tmp_clip)),
            tmp_clip,
            final_path,
        )

        run_poll(monitor, [detection()], datetime(2026, 7, 1, 10, 1, 0))

        assert monitor._departure_candidate is None
        assert not tmp_clip.exists()
        assert monitor._roosting_last_poll_detected is True

    def test_miss_reconfirm_cycles_leave_no_tmp_files(self, tmp_path):
        """Repeated miss/re-confirm cycles never accumulate .tmp files."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        monitor.roosting_mode_active = True
        monitor._roosting_last_poll_detected = True

        def fake_extract(start_time, end_time, clip_path):
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            clip_path.write_bytes(b"fake video")
            return completed_future(str(clip_path))

        monitor.clip_manager.extract_candidate_clip.side_effect = fake_extract

        base = datetime(2026, 7, 1, 10, 0, 0)
        for cycle in range(3):
            # Fresh last_detection each cycle so each candidate has its own path
            monitor.state_machine.last_detection = base + timedelta(minutes=2 * cycle)
            run_poll(monitor, [], base + timedelta(minutes=2 * cycle, seconds=30))
            run_poll(monitor, [detection()], base + timedelta(minutes=2 * cycle + 1))

        assert monitor._departure_candidate is None
        assert list(tmp_path.rglob("*.tmp")) == []


class TestCandidateFinalize:
    def _departed(self, monitor, visit_start, last_det):
        metadata = {"visit_start": visit_start, "visit_end": last_det}
        monitor.roosting_mode_active = True
        monitor._roosting_visit_metadata = {"visit_start": visit_start}
        monitor.arrival_pending = False
        monitor._handle_event(FalconEvent.DEPARTED, last_det, metadata)

    def test_departed_finalizes_candidate(self, tmp_path):
        """detect → miss → DEPARTED: .mp4.tmp renamed to .mp4; row carries the path."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        visit_start = datetime(2026, 7, 1, 8, 0, 0)
        last_det = datetime(2026, 7, 1, 10, 0, 0)

        final_path = get_output_path(str(tmp_path), last_det, "departure", "mp4")
        tmp_clip = final_path.with_suffix(".mp4.tmp")
        tmp_clip.write_bytes(b"fake video")
        monitor._departure_candidate = (
            completed_future(str(tmp_clip)),
            tmp_clip,
            final_path,
        )

        self._departed(monitor, visit_start, last_det)

        assert final_path.exists()
        assert not tmp_clip.exists()
        monitor.clip_manager.create_clip_from_buffer.assert_not_called()
        # 022-A integration: the appended row carries the finalized clip path
        visit = monitor.event_store.append.call_args[0][0]
        assert visit.departure_clip_path == str(final_path)
        assert monitor._departure_candidate is None
        assert monitor.roosting_mode_active is False

    def test_departed_without_candidate_falls_back(self, tmp_path):
        """DEPARTED with no candidate: fallback buffer extraction, no exception."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        visit_start = datetime(2026, 7, 1, 8, 0, 0)
        last_det = datetime(2026, 7, 1, 10, 0, 0)
        monitor.clip_manager.create_clip_from_buffer.return_value = False

        self._departed(monitor, visit_start, last_det)

        monitor.clip_manager.create_clip_from_buffer.assert_called_once_with(
            last_det,
            "departure",
            before_seconds=30,
            after_seconds=15,
        )
        # Visit row still appended, with no departure clip path
        visit = monitor.event_store.append.call_args[0][0]
        assert visit.departure_clip_path is None

    def test_departed_with_failed_extraction_appends_row(self, tmp_path):
        """Candidate extraction failed (no .tmp on disk): no clip, row still appended."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        visit_start = datetime(2026, 7, 1, 8, 0, 0)
        last_det = datetime(2026, 7, 1, 10, 0, 0)

        final_path = get_output_path(str(tmp_path), last_det, "departure", "mp4")
        tmp_clip = final_path.with_suffix(".mp4.tmp")  # never written
        monitor._departure_candidate = (completed_future(None), tmp_clip, final_path)

        self._departed(monitor, visit_start, last_det)

        assert not final_path.exists()
        visit = monitor.event_store.append.call_args[0][0]
        assert visit.departure_clip_path is None


class TestCandidateLifecycle:
    def test_roosting_event_arms_poll_tracking(self):
        """Entering roosting-stop mode starts from a detected-poll state."""
        monitor = make_monitor()
        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stop_recording.return_value = ("/fake/visit.mp4", {})

        monitor._handle_event(FalconEvent.ROOSTING, datetime(2026, 7, 1, 9, 0, 0), {})

        assert monitor._roosting_last_poll_detected is True
        assert monitor._departure_candidate is None

    def test_discard_with_no_candidate_is_noop(self):
        monitor = make_monitor()
        monitor._departure_candidate = None
        monitor._discard_departure_candidate()  # must not raise
        assert monitor._departure_candidate is None
