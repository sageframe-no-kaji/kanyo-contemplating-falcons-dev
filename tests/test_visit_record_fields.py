"""Tests for 022-A: peak_confidence and departure_clip_path in FalconVisit records.

Both fields exist on the dataclass and are serialized, but before 022-A neither
FalconVisit construction site in buffer_monitor populated them — all production
rows showed peak_confidence 0.0 and departure_clip_path null.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.detection.event_types import FalconEvent
from kanyo.detection.events import FalconVisit
from kanyo.utils.output import get_output_path


def make_monitor(clips_dir: str = "clips", roosting_recording_mode: str = "continuous"):
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
            clips_dir=clips_dir,
            full_config={
                "roosting_recording_mode": roosting_recording_mode,
                "roosting_detection_interval": 30,
                "arrival_confirmation_seconds": 10,
                "arrival_confirmation_ratio": 0.3,
            },
        )
    return monitor


def detection(conf: float):
    """Minimal stand-in for a Detection with a confidence attribute."""
    return SimpleNamespace(confidence=conf)


def _quiet_frame_mocks(monitor) -> None:
    """Configure recorder/buffer mocks so process_frame runs the poll path."""
    monitor.visit_recorder.is_recording = False
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor.frame_buffer.add_frame.return_value = None
    monitor.state_machine.update.return_value = []


class TestPeakConfidenceTracking:
    def test_process_frame_tracks_running_max(self):
        """The visit peak is the max confidence across all detection polls."""
        monitor = make_monitor()
        _quiet_frame_mocks(monitor)
        monitor.detector.detect_birds.side_effect = [
            [detection(0.4)],
            [detection(0.8), detection(0.5)],
            [],
            [detection(0.6)],
        ]

        now = datetime(2026, 7, 1, 10, 0, 0)
        frame = MagicMock()
        frame.shape = (720, 1280, 3)
        with patch("kanyo.detection.buffer_monitor.get_now_tz", return_value=now):
            for n in range(4):
                monitor.process_frame(frame, frame_number=n)

        assert monitor._visit_peak_confidence == 0.8

    def test_arrived_seeds_peak_from_arriving_frame(self):
        """ARRIVED discards any stale peak and seeds with the arriving frame's confidence."""
        monitor = make_monitor()
        monitor._visit_peak_confidence = 0.99  # stale value from a prior visit
        monitor._frame_peak_confidence = 0.55  # arriving frame's poll
        monitor.frame_buffer.get_frames_before.return_value = []

        monitor._handle_event(FalconEvent.ARRIVED, datetime(2026, 7, 1, 10, 0, 0), {})

        assert monitor._visit_peak_confidence == 0.55

    def test_cancel_arrival_resets_peak(self):
        monitor = make_monitor()
        monitor._visit_peak_confidence = 0.7
        monitor.arrival_clip_recorder.get_temp_path.return_value = None
        monitor.visit_recorder.get_temp_path.return_value = None

        monitor._cancel_arrival(ratio=0.1)

        assert monitor._visit_peak_confidence == 0.0

    def test_reset_pending_states_resets_peak(self):
        """Outage-exceeded and cancelled-startup paths discard the visit peak."""
        monitor = make_monitor()
        monitor._visit_peak_confidence = 0.7

        monitor._reset_pending_states()

        assert monitor._visit_peak_confidence == 0.0


class TestNormalDepartureVisitRecord:
    def _departure(self, monitor, visit_start, visit_end, clip_scheduled: bool):
        monitor.arrival_pending = False
        monitor.arrival_clip_recorder.is_recording.return_value = False
        monitor.visit_recorder.stop_recording.return_value = (
            "/fake/visit.mp4",
            {"visit_start": visit_start, "duration_seconds": 1200},
        )
        monitor.clip_manager.create_departure_clip.return_value = clip_scheduled

        metadata = {"visit_start": visit_start, "visit_end": visit_end}
        monitor._handle_event(FalconEvent.DEPARTED, visit_end + timedelta(seconds=90), metadata)

    def test_row_carries_peak_and_departure_clip_path(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path))
        monitor._visit_peak_confidence = 0.77
        visit_start = datetime(2026, 7, 1, 10, 0, 0)
        visit_end = datetime(2026, 7, 1, 10, 20, 0)

        self._departure(monitor, visit_start, visit_end, clip_scheduled=True)

        monitor.event_store.append.assert_called_once()
        visit = monitor.event_store.append.call_args[0][0]
        assert isinstance(visit, FalconVisit)
        assert visit.peak_confidence == 0.77
        expected = str(get_output_path(str(tmp_path), visit_end, "departure", "mp4"))
        assert visit.departure_clip_path == expected

    def test_departure_clip_path_none_when_scheduling_failed(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path))
        monitor._visit_peak_confidence = 0.42
        visit_start = datetime(2026, 7, 1, 10, 0, 0)
        visit_end = datetime(2026, 7, 1, 10, 20, 0)

        self._departure(monitor, visit_start, visit_end, clip_scheduled=False)

        visit = monitor.event_store.append.call_args[0][0]
        assert visit.peak_confidence == 0.42
        assert visit.departure_clip_path is None

    def test_peak_resets_between_visits(self, tmp_path):
        """A second visit does not inherit the first visit's peak."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        monitor._visit_peak_confidence = 0.9
        visit_start = datetime(2026, 7, 1, 10, 0, 0)
        visit_end = datetime(2026, 7, 1, 10, 20, 0)

        self._departure(monitor, visit_start, visit_end, clip_scheduled=True)
        assert monitor._visit_peak_confidence == 0.0

        # Second visit arrives with a weaker detection
        monitor._frame_peak_confidence = 0.3
        monitor.frame_buffer.get_frames_before.return_value = []
        monitor._handle_event(FalconEvent.ARRIVED, datetime(2026, 7, 1, 11, 0, 0), {})
        assert monitor._visit_peak_confidence == 0.3

    def test_parses_isoformat_visit_end(self, tmp_path):
        """String visit_end is parsed for the departure clip path derivation.

        visit_start stays a datetime: FalconVisit.__post_init__ derives its id
        via start_time.strftime, so a string start_time is not constructible
        (pre-existing constraint; the state machine always emits datetimes).
        """
        monitor = make_monitor(clips_dir=str(tmp_path))
        visit_start = datetime(2026, 7, 1, 10, 0, 0)
        visit_end = datetime(2026, 7, 1, 10, 20, 0)

        monitor.arrival_pending = False
        monitor.arrival_clip_recorder.is_recording.return_value = False
        monitor.visit_recorder.stop_recording.return_value = (
            "/fake/visit.mp4",
            {"visit_start": visit_start, "duration_seconds": 1200},
        )
        monitor.clip_manager.create_departure_clip.return_value = True

        metadata = {
            "visit_start": visit_start,
            "visit_end": visit_end.isoformat(),
        }
        monitor._handle_event(FalconEvent.DEPARTED, visit_end + timedelta(seconds=90), metadata)

        visit = monitor.event_store.append.call_args[0][0]
        expected = str(get_output_path(str(tmp_path), visit_end, "departure", "mp4"))
        assert visit.departure_clip_path == expected


class TestRoostingStopDepartureVisitRecord:
    def _roost_departure(self, monitor, visit_start, visit_end, clip_scheduled: bool):
        monitor.roosting_mode_active = True
        monitor._roosting_visit_metadata = {"visit_start": visit_start}
        monitor.clip_manager.clip_departure_before = 30
        monitor.clip_manager.clip_departure_after = 15
        monitor.clip_manager.create_clip_from_buffer.return_value = clip_scheduled
        monitor.arrival_pending = False
        monitor.arrival_clip_recorder.is_recording.return_value = False

        metadata = {"visit_start": visit_start, "visit_end": visit_end}
        monitor._handle_event(FalconEvent.DEPARTED, visit_end, metadata)

    def test_row_carries_peak_and_departure_clip_path(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path), roosting_recording_mode="stop")
        monitor._visit_peak_confidence = 0.66
        visit_start = datetime(2026, 7, 1, 8, 0, 0)
        visit_end = datetime(2026, 7, 1, 11, 0, 0)

        self._roost_departure(monitor, visit_start, visit_end, clip_scheduled=True)

        monitor.event_store.append.assert_called_once()
        visit = monitor.event_store.append.call_args[0][0]
        assert isinstance(visit, FalconVisit)
        assert visit.peak_confidence == 0.66
        expected = str(get_output_path(str(tmp_path), visit_end, "departure", "mp4"))
        assert visit.departure_clip_path == expected

    def test_departure_clip_path_none_when_scheduling_failed(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path), roosting_recording_mode="stop")
        monitor._visit_peak_confidence = 0.66
        visit_start = datetime(2026, 7, 1, 8, 0, 0)
        visit_end = datetime(2026, 7, 1, 11, 0, 0)

        self._roost_departure(monitor, visit_start, visit_end, clip_scheduled=False)

        visit = monitor.event_store.append.call_args[0][0]
        assert visit.departure_clip_path is None

    def test_peak_reset_after_roosting_departure(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path), roosting_recording_mode="stop")
        monitor._visit_peak_confidence = 0.66
        visit_start = datetime(2026, 7, 1, 8, 0, 0)
        visit_end = datetime(2026, 7, 1, 11, 0, 0)

        self._roost_departure(monitor, visit_start, visit_end, clip_scheduled=True)

        assert monitor._visit_peak_confidence == 0.0
