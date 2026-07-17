"""Integration tests for bird count tracking in BufferMonitor (issue #3).

Drives BufferMonitor.process_frame with a mocked detector and synthetic
numpy frames, with the real PresenceTracker, BirdCountTracker,
FalconStateMachine, and EventSignificanceFilter in the loop. Recording/clip
components are mocked — count judgment and its surface are under test, not
recording mechanics.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

import numpy as np
import pytest

from kanyo.detection.bird_count import BirdCountTracker
from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.detection.detect import Detection
from kanyo.detection.event_types import FalconEvent
from kanyo.detection.events import FalconVisit
from kanyo.detection.falcon_state import EventMetadata
from kanyo.detection.significance_filter import EventSignificanceFilter

FRAME_H = 240
FRAME_W = 320
BLOB = (100, 100, 140, 140)
BLOB_2 = (200, 60, 240, 100)
T0 = datetime(2026, 7, 16, 12, 0, 0)

EXIT_TIMEOUT = 90
CONFIRMATION_SECONDS = 4
CONFIRMATION_RATIO = 0.5
COUNT_WINDOW = 10


def ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_frame(*blobs: tuple[int, int, int, int]) -> np.ndarray:
    """Dark frame with bright rectangular blobs (x1, y1, x2, y2)."""
    frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
    for x1, y1, x2, y2 in blobs:
        frame[y1:y2, x1:x2] = 200
    return frame


def bird(confidence: float, when: datetime, bbox: tuple[int, int, int, int] = BLOB) -> Detection:
    return Detection(
        class_id=14, class_name="bird", confidence=confidence, bbox=bbox, timestamp=when
    )


def make_monitor(bird_count_enabled: bool = True, **kwargs) -> BufferMonitor:
    """BufferMonitor with mocked detector/recording components and real
    state machine, presence tracker, and count tracker."""
    monitor = BufferMonitor(
        stream_url="test",
        exit_timeout_seconds=EXIT_TIMEOUT,
        presence_enabled=True,
        bird_count_enabled=bird_count_enabled,
        bird_count_confirmation_seconds=COUNT_WINDOW,
        full_config={
            "arrival_confirmation_seconds": CONFIRMATION_SECONDS,
            "arrival_confirmation_ratio": CONFIRMATION_RATIO,
        },
        **kwargs,
    )

    monitor.detector = Mock()

    monitor.visit_recorder = Mock()
    monitor.visit_recorder.is_recording = False
    monitor.visit_recorder.stream_outage_exceeded = False
    monitor.visit_recorder.lead_in_seconds = 15
    monitor.visit_recorder.stop_recording.return_value = (None, None)
    monitor.visit_recorder.get_temp_path.return_value = None

    monitor.arrival_clip_recorder = Mock()
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor.arrival_clip_recorder.get_temp_path.return_value = None

    monitor.event_handler = Mock()
    monitor.event_store = Mock()
    monitor.clip_manager = Mock()
    monitor.clip_manager.clip_departure_before = 30
    monitor.clip_manager.create_departure_clip.return_value = False

    monitor.state_machine.initializing = False
    return monitor


def drive(
    monitor: BufferMonitor,
    frame: np.ndarray,
    when: datetime,
    filtered: list[Detection],
    raw: list[Detection] | None = None,
) -> None:
    raw = filtered if raw is None else raw
    monitor.detector.detect_with_raw.return_value = (filtered, raw)
    monitor.detector.detect_birds.return_value = filtered
    monitor.process_frame(frame, 0, when)


def one_bird_baseline(monitor: BufferMonitor) -> float:
    """Arrive, confirm, and confirm the count at 1. Returns last offset."""
    frame = make_frame(BLOB)
    t = 0.0
    drive(monitor, frame, ts(t), [bird(0.8, ts(t))])
    while monitor.arrival_pending:
        t += 1.0
        drive(monitor, frame, ts(t), [bird(0.8, ts(t))])
    while t < COUNT_WINDOW + 1:
        t += 1.0
        drive(monitor, frame, ts(t), [bird(0.8, ts(t))])
    assert monitor.bird_count is not None
    assert monitor.bird_count.confirmed_count == 1
    return t


def count_change_calls(monitor: BufferMonitor) -> list:
    return [
        call
        for call in monitor.event_handler.handle_event.call_args_list
        if call.args[0] == FalconEvent.COUNT_CHANGED
    ]


class TestConstruction:
    def test_enabled_builds_tracker(self):
        monitor = make_monitor(bird_count_enabled=True)
        assert isinstance(monitor.bird_count, BirdCountTracker)
        assert monitor.bird_count.confirmation_seconds == COUNT_WINDOW

    def test_disabled_by_default(self):
        monitor = BufferMonitor(stream_url="test")
        assert monitor.bird_count is None

    def test_disabled_visit_max_is_none(self):
        monitor = make_monitor(bird_count_enabled=False)
        assert monitor._visit_max_birds() is None


class TestCountJudgment:
    def test_second_bird_sustained_confirms_and_surfaces(self):
        monitor = make_monitor()
        t = one_bird_baseline(monitor)

        two = make_frame(BLOB, BLOB_2)
        end = t + COUNT_WINDOW + 2
        while t < end:
            t += 1.0
            drive(
                monitor,
                two,
                ts(t),
                [bird(0.8, ts(t)), bird(0.7, ts(t), BLOB_2)],
            )

        assert monitor.bird_count.confirmed_count == 2
        calls = count_change_calls(monitor)
        assert len(calls) == 1
        metadata = calls[0].args[2]
        assert metadata == {"old_count": 1, "new_count": 2}

    def test_single_poll_flicker_does_not_change_count(self):
        monitor = make_monitor()
        t = one_bird_baseline(monitor)

        # One poll with a phantom second box, then back to one bird.
        t += 1.0
        drive(
            monitor, make_frame(BLOB, BLOB_2), ts(t), [bird(0.8, ts(t)), bird(0.6, ts(t), BLOB_2)]
        )
        for _ in range(30):
            t += 1.0
            drive(monitor, make_frame(BLOB), ts(t), [bird(0.8, ts(t))])

        assert monitor.bird_count.confirmed_count == 1
        assert count_change_calls(monitor) == []

    def test_parked_dropout_holds_count(self):
        """No boxes at any threshold on a static frame: presence holds
        (ho-12) and the count holds with it."""
        monitor = make_monitor()
        t = one_bird_baseline(monitor)

        frame = make_frame(BLOB)
        end = t + 120
        while t < end:
            t += 5.0
            drive(monitor, frame, ts(t), [], [])

        assert monitor.state_machine.state.value == "visiting"
        assert monitor.bird_count.confirmed_count == 1
        assert count_change_calls(monitor) == []

    def test_sustain_boxes_carry_count_through_recognition_dropout(self):
        """Filtered view empty, two sustain-level any-class boxes: the count
        stays 2 instead of collapsing (continuity evidence)."""
        monitor = make_monitor()
        t = one_bird_baseline(monitor)

        two = make_frame(BLOB, BLOB_2)
        end = t + COUNT_WINDOW + 2
        while t < end:
            t += 1.0
            drive(monitor, two, ts(t), [bird(0.8, ts(t)), bird(0.7, ts(t), BLOB_2)])
        assert monitor.bird_count.confirmed_count == 2

        # Recognition drops to sustain-level only ("elephant" boxes) —
        # count must not decay.
        elephant = [
            Detection(20, "elephant", 0.3, BLOB, ts(t)),
            Detection(20, "elephant", 0.25, BLOB_2, ts(t)),
        ]
        end = t + COUNT_WINDOW * 3
        while t < end:
            t += 1.0
            drive(monitor, two, ts(t), [], elephant)

        assert monitor.bird_count.confirmed_count == 2
        assert len(count_change_calls(monitor)) == 1  # only the 1→2

    def test_zero_boundary_changes_never_surface_as_count_events(self):
        """The 0→1 confirmation and the departure back to 0 belong to the
        arrival/departure surface, not COUNT_CHANGED."""
        monitor = make_monitor()
        t = one_bird_baseline(monitor)  # 0→1 confirmed inside

        # Bird leaves: motion burst then quiet, exit timeout runs.
        empty = make_frame()
        end = t + EXIT_TIMEOUT + 30
        while t < end:
            t += 5.0
            drive(monitor, empty, ts(t), [], [])

        assert monitor.state_machine.state.value == "absent"
        assert count_change_calls(monitor) == []
        assert monitor.bird_count.confirmed_count == 0  # reset with episode


class TestVisitRowMax:
    def _depart(self, monitor: BufferMonitor, t: float) -> float:
        empty = make_frame()
        end = t + EXIT_TIMEOUT + 30
        while t < end:
            t += 5.0
            drive(monitor, empty, ts(t), [], [])
        assert monitor.state_machine.state.value == "absent"
        return t

    def _recorded_row(self, monitor: BufferMonitor) -> FalconVisit:
        """The finalized visit row (the confirm also upserts a provisional)."""
        finalized = [
            call.args[0]
            for call in monitor.event_store.upsert.call_args_list
            if call.args[0].end_time is not None
        ]
        assert len(finalized) == 1
        row = finalized[0]
        assert isinstance(row, FalconVisit)
        return row

    def _arm_visit_row(self, monitor: BufferMonitor, visit_start: datetime) -> None:
        """Make stop_recording produce metadata so a row is constructed."""
        monitor.visit_recorder.stop_recording.return_value = (
            "/fake/visit.mp4",
            {"visit_start": visit_start, "duration_seconds": 60},
        )

    def test_row_records_max_concurrent(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path))
        t = one_bird_baseline(monitor)

        two = make_frame(BLOB, BLOB_2)
        end = t + COUNT_WINDOW + 2
        while t < end:
            t += 1.0
            drive(monitor, two, ts(t), [bird(0.8, ts(t)), bird(0.7, ts(t), BLOB_2)])
        assert monitor.bird_count.confirmed_count == 2

        self._arm_visit_row(monitor, T0)
        self._depart(monitor, t)

        assert self._recorded_row(monitor).max_concurrent_birds == 2

    def test_row_floors_at_one_when_no_confirmation(self, tmp_path):
        """A visit shorter than the count window still had one bird."""
        monitor = make_monitor(clips_dir=str(tmp_path))
        frame = make_frame(BLOB)
        t = 0.0
        drive(monitor, frame, ts(t), [bird(0.8, ts(t))])
        while monitor.arrival_pending:
            t += 1.0
            drive(monitor, frame, ts(t), [bird(0.8, ts(t))])
        # Depart before the 0→1 count confirmation lands.
        assert monitor.bird_count.confirmed_count == 0

        self._arm_visit_row(monitor, T0)
        self._depart(monitor, t)

        assert self._recorded_row(monitor).max_concurrent_birds == 1

    def test_merge_takes_max_across_segments(self):
        monitor = make_monitor()
        first = FalconVisit(start_time=T0, end_time=ts(60), max_concurrent_birds=2)
        second = FalconVisit(start_time=ts(300), end_time=ts(400), max_concurrent_birds=1)
        monitor._merge_pending_visit_row(first)
        monitor._merge_pending_visit_row(second)
        assert monitor._pending_visit_row is not None
        assert monitor._pending_visit_row.max_concurrent_birds == 2

    def test_merge_keeps_none_when_count_disabled(self):
        monitor = make_monitor(bird_count_enabled=False)
        first = FalconVisit(start_time=T0, end_time=ts(60))
        second = FalconVisit(start_time=ts(300), end_time=ts(400))
        monitor._merge_pending_visit_row(first)
        monitor._merge_pending_visit_row(second)
        assert monitor._pending_visit_row.max_concurrent_birds is None


class TestSignificanceFilterCountDecisions:
    def _event(self, when: datetime) -> tuple:
        metadata: EventMetadata = {"old_count": 1, "new_count": 2}
        return (FalconEvent.COUNT_CHANGED, when, metadata)

    def test_pass_through_notifies_never_records(self):
        filt = EventSignificanceFilter(enabled=True)
        decisions = filt.process(self._event(T0), T0)
        assert len(decisions) == 1
        assert decisions[0].notify is True
        assert decisions[0].record is False

    def test_disabled_filter_passes_through(self):
        filt = EventSignificanceFilter(enabled=False)
        decisions = filt.process(self._event(T0), T0)
        assert decisions[0].notify is True

    def test_damped_mode_suppresses_count_notifications(self):
        filt = EventSignificanceFilter(
            enabled=True, damping_arrivals_threshold=2, damping_window_hours=1
        )
        # Blow past the damping threshold with pass-through arrivals.
        for i in range(4):
            filt.process((FalconEvent.ARRIVED, ts(i * 60), {}), ts(i * 60))
        assert filt.state_info()["damped"] is True

        decisions = filt.process(self._event(ts(300)), ts(300))
        assert decisions[0].notify is False
        assert decisions[0].record is False


class TestConfig:
    def test_defaults(self):
        from kanyo.utils.config import DEFAULTS

        assert DEFAULTS["bird_count_enabled"] is False
        assert DEFAULTS["bird_count_confirmation_seconds"] == 10

    @pytest.mark.parametrize("bad_value", [0, -5, "fast"])
    def test_bad_confirmation_seconds_rejected(self, bad_value):
        from kanyo.utils.config import _validate

        cfg = {
            "video_source": "https://youtube.com/test",
            "bird_count_confirmation_seconds": bad_value,
        }
        with pytest.raises(ValueError, match="bird_count_confirmation_seconds"):
            _validate(cfg)

    def test_valid_config_passes(self):
        from kanyo.utils.config import _validate

        cfg = {
            "video_source": "https://youtube.com/test",
            "bird_count_enabled": True,
            "bird_count_confirmation_seconds": 20,
        }
        _validate(cfg)  # should not raise


class TestEventHandlerCountChanged:
    def test_notifies_with_counts(self):
        from kanyo.detection.event_handler import FalconEventHandler

        handler = FalconEventHandler(notifications=Mock())
        handler.handle_event(FalconEvent.COUNT_CHANGED, T0, {"old_count": 1, "new_count": 2})
        handler.notifications.send_count_change.assert_called_once_with(T0, 1, 2)

    def test_no_notifications_configured(self):
        from kanyo.detection.event_handler import FalconEventHandler

        handler = FalconEventHandler(notifications=None)
        # Must not raise without a notification manager.
        handler.handle_event(FalconEvent.COUNT_CHANGED, T0, {"old_count": 2, "new_count": 1})


class TestCountChangeNotification:
    def test_disabled_telegram_returns_false(self):
        from kanyo.utils.notifications import NotificationManager

        manager = NotificationManager({"telegram_enabled": False})
        assert manager.send_count_change(T0, 1, 2) is False

    def test_increase_and_decrease_messages(self, monkeypatch):
        from kanyo.utils.notifications import NotificationManager

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
        manager = NotificationManager({"telegram_enabled": True, "telegram_channel": "@test"})
        sent: list[str] = []
        manager._send_telegram_text = lambda text: sent.append(text) or True

        assert manager.send_count_change(T0, 1, 2) is True
        assert manager.send_count_change(T0, 2, 1) is True
        assert "Another falcon arrived" in sent[0]
        assert "2 birds in view" in sent[0]
        assert "One falcon left" in sent[1]
        assert "1 bird still in view" in sent[1]
