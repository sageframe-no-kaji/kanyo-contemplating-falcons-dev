"""Integration tests for the presence layer wiring in BufferMonitor (024-C).

Drives BufferMonitor.process_frame with a mocked detector and synthetic
numpy frames, with the real PresenceTracker and FalconStateMachine in the
loop. Recording/clip components are mocked — event sequences and
confirmation counters are what's under test, not recording mechanics.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

import numpy as np
import pytest

from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.detection.detect import Detection
from kanyo.detection.event_types import FalconEvent
from kanyo.detection.presence import PresenceTracker

FRAME_H = 240
FRAME_W = 320
BLOB = (100, 100, 140, 140)
T0 = datetime(2026, 7, 16, 12, 0, 0)

EXIT_TIMEOUT = 90
CONFIRMATION_SECONDS = 4
CONFIRMATION_RATIO = 0.5


def ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_frame(blob: tuple[int, int, int, int] | None = BLOB) -> np.ndarray:
    """Dark frame with an optional bright rectangular blob (x1, y1, x2, y2)."""
    frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
    if blob is not None:
        x1, y1, x2, y2 = blob
        frame[y1:y2, x1:x2] = 200
    return frame


def bird(confidence: float, when: datetime) -> Detection:
    return Detection(
        class_id=14, class_name="bird", confidence=confidence, bbox=BLOB, timestamp=when
    )


def make_monitor(presence_enabled: bool) -> BufferMonitor:
    """BufferMonitor with mocked detector/recording components and a real
    state machine (and real PresenceTracker when enabled)."""
    monitor = BufferMonitor(
        stream_url="test",
        exit_timeout_seconds=EXIT_TIMEOUT,
        presence_enabled=presence_enabled,
        full_config={
            "arrival_confirmation_seconds": CONFIRMATION_SECONDS,
            "arrival_confirmation_ratio": CONFIRMATION_RATIO,
        },
    )

    # Detector is scripted per frame via drive().
    monitor.detector = Mock()

    # Recording/clip components are not under test.
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
    monitor.clip_manager = Mock()
    monitor.clip_manager.clip_departure_before = 30
    monitor.clip_manager.create_departure_clip.return_value = False

    # Exit initialization mode so ARRIVED events fire.
    monitor.state_machine.initializing = False

    return monitor


def spy_events(monitor: BufferMonitor) -> list[FalconEvent]:
    """Record every event type routed through _handle_event, in order."""
    events: list[FalconEvent] = []
    original = monitor._handle_event

    def recording_handler(event_type, event_time, metadata):
        events.append(event_type)
        original(event_type, event_time, metadata)

    monitor._handle_event = recording_handler  # type: ignore[method-assign]  # test spy
    return events


def drive(
    monitor: BufferMonitor,
    frame: np.ndarray,
    when: datetime,
    filtered: list[Detection],
    raw: list[Detection] | None = None,
) -> None:
    """Process one frame with scripted detector output."""
    raw = filtered if raw is None else raw
    monitor.detector.detect_with_raw.return_value = (filtered, raw)
    monitor.detector.detect_birds.return_value = filtered
    monitor.process_frame(frame, 0, when)


def arrive_and_confirm(monitor: BufferMonitor) -> float:
    """Drive a clean arrival through the confirmation window on static blob
    frames. Returns the timestamp offset (seconds) of the last driven frame."""
    frame = make_frame(BLOB)
    t = 0.0
    drive(monitor, frame, ts(t), [bird(0.8, ts(t))])  # ARRIVED
    while monitor.arrival_pending:
        t += 1.0
        drive(monitor, frame, ts(t), [bird(0.8, ts(t))])
    return t


class TestConstruction:
    """Constructor wiring behind presence_enabled."""

    def test_enabled_builds_tracker_and_raw_floor(self):
        monitor = BufferMonitor(
            stream_url="test",
            presence_enabled=True,
            presence_sustain_confidence=0.2,
            presence_region_margin_frac=0.3,
            presence_motion_pixel_threshold=30,
            presence_motion_min_area_frac=0.05,
            presence_global_change_frac=0.6,
            presence_absence_failsafe_seconds=7200,
        )
        assert isinstance(monitor.presence, PresenceTracker)
        assert monitor.detector.raw_floor_confidence == 0.2
        assert monitor.presence.sustain_confidence == 0.2
        assert monitor.presence.region_margin_frac == 0.3
        assert monitor.presence.motion_pixel_threshold == 30
        assert monitor.presence.motion_min_area_frac == 0.05
        assert monitor.presence.global_change_frac == 0.6
        assert monitor.presence.absence_failsafe_seconds == 7200

    def test_enabled_by_default(self):
        monitor = BufferMonitor(stream_url="test")
        assert isinstance(monitor.presence, PresenceTracker)

    def test_disabled_builds_no_tracker_no_raw_floor(self):
        monitor = BufferMonitor(stream_url="test", presence_enabled=False)
        assert monitor.presence is None
        assert monitor.detector.raw_floor_confidence is None


class TestDisabledLegacyBehavior:
    """presence_enabled: false must reproduce the pre-presence pipeline."""

    def test_dropout_manufactures_departed_as_today(self):
        """The legacy event sequence for 'arrival, dropout past exit_timeout':
        ARRIVED then DEPARTED — the manufactured exit the presence layer
        exists to fix stays intact when the layer is off."""
        monitor = make_monitor(presence_enabled=False)
        events = spy_events(monitor)

        t = arrive_and_confirm(monitor)
        assert events == [FalconEvent.ARRIVED]

        # Detection dropout on a static frame — legacy behavior departs.
        frame = make_frame(BLOB)
        end = t + EXIT_TIMEOUT + 20
        while t < end:
            t += 5.0
            drive(monitor, frame, ts(t), [])

        assert FalconEvent.DEPARTED in events
        assert monitor.state_machine.state.value == "absent"

    def test_disabled_never_calls_detect_with_raw(self):
        monitor = make_monitor(presence_enabled=False)
        drive(monitor, make_frame(BLOB), ts(0), [bird(0.8, ts(0))])
        drive(monitor, make_frame(BLOB), ts(1), [])

        monitor.detector.detect_birds.assert_called()
        monitor.detector.detect_with_raw.assert_not_called()


class TestEnabledSustain:
    """The core fix: detection dropout on a parked bird must not depart."""

    def test_dropout_on_parked_frame_produces_no_departed(self):
        monitor = make_monitor(presence_enabled=True)
        events = spy_events(monitor)

        t = arrive_and_confirm(monitor)
        assert events == [FalconEvent.ARRIVED]

        # Total detection dropout, static (parked) frame, far past
        # exit_timeout: no detection AND no motion = still present.
        frame = make_frame(BLOB)
        end = t + EXIT_TIMEOUT * 3
        while t < end:
            t += 5.0
            drive(monitor, frame, ts(t), [], [])

        assert FalconEvent.DEPARTED not in events
        assert monitor.state_machine.state.value == "visiting"

    def test_sustain_keeps_last_detection_time_current(self):
        """With presence enabled, last_detection_time reflects presence
        evidence — intended semantic shift (ho-12)."""
        monitor = make_monitor(presence_enabled=True)
        t = arrive_and_confirm(monitor)

        frame = make_frame(BLOB)
        t += 30.0
        drive(monitor, frame, ts(t), [], [])
        assert monitor.last_detection_time == ts(t)


class TestEnabledDeparture:
    """Exit still works: motion out of the region then quiet allows the
    normal exit_timeout departure."""

    def test_motion_out_then_quiet_departs(self):
        monitor = make_monitor(presence_enabled=True)
        events = spy_events(monitor)

        t = arrive_and_confirm(monitor)

        # Motion burst: the blob vanishes (bird leaves). One poll of region
        # motion, then a quiet empty nest with no detections at any level.
        empty = make_frame(None)
        t += 5.0
        drive(monitor, empty, ts(t), [], [])

        end = t + EXIT_TIMEOUT + 30
        while t < end:
            t += 5.0
            drive(monitor, empty, ts(t), [], [])

        assert FalconEvent.DEPARTED in events
        assert monitor.state_machine.state.value == "absent"

    def test_departed_resets_tracker_episode(self):
        """After DEPARTED the tracker's episode is closed: a new presence can
        only begin with a strict ENTER (filtered detection)."""
        monitor = make_monitor(presence_enabled=True)
        events = spy_events(monitor)

        t = arrive_and_confirm(monitor)
        empty = make_frame(None)
        end = t + EXIT_TIMEOUT + 40
        while t < end:
            t += 5.0
            drive(monitor, empty, ts(t), [], [])
        assert FalconEvent.DEPARTED in events

        assert monitor.presence is not None
        assert monitor.presence.state_info()["episode_active"] is False


class TestEnterSemantics:
    """Arrival confirmation stays keyed to filtered YOLO hits (ENTER path
    unchanged), in both modes."""

    def test_clean_arrival_counters_identical_in_both_modes(self):
        frame = make_frame(BLOB)
        script: list[list[Detection]] = [
            [bird(0.8, ts(0))],  # ARRIVED
            [bird(0.7, ts(1))],
            [],  # recognition gap inside the window
            [bird(0.9, ts(3))],
        ]

        counts = {}
        for enabled in (True, False):
            monitor = make_monitor(presence_enabled=enabled)
            for i, filtered in enumerate(script):
                drive(monitor, frame, ts(i), filtered)
            counts[enabled] = (
                monitor.arrival_detection_count,
                monitor.arrival_frame_count,
            )

        # Seeded 1/1 by ARRIVED, then +det, +miss, +det → 3/4 in both modes.
        assert counts[True] == counts[False] == (3, 4)

    def test_parked_sustain_does_not_rescue_failed_confirmation(self):
        """A single-frame ENTER followed by nothing but parked-sustain must
        still fail confirmation — the counters count YOLO hits, not the
        presence boolean — and the cancel resets the tracker."""
        monitor = make_monitor(presence_enabled=True)
        frame = make_frame(BLOB)

        drive(monitor, frame, ts(0), [bird(0.8, ts(0))])  # ARRIVED
        assert monitor.arrival_pending is True

        # No detections at all; the parked frame sustains presence, so the
        # state machine keeps seeing True — but the confirmation ratio
        # collapses: 1 hit / 6 frames < 0.5.
        t = 0.0
        while monitor.arrival_pending:
            t += 1.0
            drive(monitor, frame, ts(t), [], [])

        assert monitor.state_machine.state.value == "absent"
        assert monitor.presence is not None
        assert monitor.presence.state_info()["episode_active"] is False


class TestResetHooks:
    """Tracker and state machine cannot disagree after a force-reset."""

    def _present_monitor(self) -> BufferMonitor:
        monitor = make_monitor(presence_enabled=True)
        arrive_and_confirm(monitor)
        assert monitor.presence is not None
        assert monitor.presence.state_info()["episode_active"] is True
        return monitor

    def test_reset_pending_states_resets_tracker(self):
        """Covers the outage-exceeded paths and cancelled startup, which all
        flow through _reset_pending_states."""
        monitor = self._present_monitor()
        monitor._reset_pending_states()
        assert monitor.presence.state_info()["episode_active"] is False

    def test_cancel_recovery_resets_tracker(self):
        monitor = self._present_monitor()
        events = spy_events(monitor)

        monitor.state_machine.set_pending_recovery(ts(100))
        monitor._cancel_recovery(0.0, ts(120))

        assert FalconEvent.DEPARTED in events
        assert monitor.presence.state_info()["episode_active"] is False
        assert monitor.state_machine.state.value == "absent"

    def test_disabled_reset_is_noop(self):
        monitor = make_monitor(presence_enabled=False)
        monitor._reset_presence()  # must not raise with presence=None
        monitor._reset_pending_states()


class TestPresenceConfig:
    """Config keys, defaults, and validation (024-C)."""

    def test_defaults_contain_all_presence_keys(self):
        from kanyo.utils.config import DEFAULTS

        assert DEFAULTS["presence_enabled"] is True
        assert DEFAULTS["presence_sustain_confidence"] == 0.15
        assert DEFAULTS["presence_region_margin_frac"] == 0.25
        assert DEFAULTS["presence_motion_pixel_threshold"] == 25
        assert DEFAULTS["presence_motion_min_area_frac"] == 0.02
        assert DEFAULTS["presence_global_change_frac"] == 0.5
        assert DEFAULTS["presence_absence_failsafe_seconds"] == 3600

    @pytest.mark.parametrize(
        "key",
        [
            "presence_sustain_confidence",
            "presence_region_margin_frac",
            "presence_motion_min_area_frac",
            "presence_global_change_frac",
        ],
    )
    @pytest.mark.parametrize("bad_value", [-0.1, 1.5, "high"])
    def test_fraction_out_of_range_rejected(self, key, bad_value):
        from kanyo.utils.config import _validate

        cfg = {"video_source": "https://youtube.com/test", key: bad_value}
        with pytest.raises(ValueError, match=f"{key} must be between 0.0 and 1.0"):
            _validate(cfg)

    @pytest.mark.parametrize("bad_value", [-1, 256])
    def test_pixel_threshold_out_of_range_rejected(self, bad_value):
        from kanyo.utils.config import _validate

        cfg = {
            "video_source": "https://youtube.com/test",
            "presence_motion_pixel_threshold": bad_value,
        }
        with pytest.raises(ValueError, match="presence_motion_pixel_threshold"):
            _validate(cfg)

    def test_failsafe_must_exceed_exit_timeout(self):
        from kanyo.utils.config import _validate

        cfg = {
            "video_source": "https://youtube.com/test",
            "exit_timeout": 300,
            "roosting_threshold": 1800,
            "presence_absence_failsafe_seconds": 300,
        }
        with pytest.raises(ValueError, match="presence_absence_failsafe_seconds"):
            _validate(cfg)

    def test_failsafe_constraint_inert_when_disabled(self):
        """With the presence layer off the failsafe key is unused and must
        not block an otherwise-valid legacy config."""
        from kanyo.utils.config import _validate

        cfg = {
            "video_source": "https://youtube.com/test",
            "exit_timeout": 300,
            "roosting_threshold": 1800,
            "presence_enabled": False,
            "presence_absence_failsafe_seconds": 300,
        }
        _validate(cfg)  # should not raise

    def test_valid_presence_config_passes(self):
        from kanyo.utils.config import _validate

        cfg = {
            "video_source": "https://youtube.com/test",
            "presence_enabled": True,
            "presence_sustain_confidence": 0.1,
            "presence_region_margin_frac": 0.5,
            "presence_motion_pixel_threshold": 35,
            "presence_motion_min_area_frac": 0.05,
            "presence_global_change_frac": 0.7,
            "presence_absence_failsafe_seconds": 1800,
        }
        _validate(cfg)  # should not raise
