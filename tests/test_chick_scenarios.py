"""Synthetic chick-cam scenarios for the presence and count layers (issue #2).

Issue #2 asks how the system behaves on nests with active chicks before real
breeding-season footage exists. These tests answer the state-machine half of
its questions synthetically: several low-confidence boxes near the presence
region, appearing and disappearing poll to poll, with chick-wriggle motion —
verifying no exit churn (the always-occupied nest never manufactures
DEPARTED/ARRIVED pairs) and sane counting (flicker never confirms, sustained
changes confirm exactly once).

Same harness style as test_presence_integration / test_bird_count_integration:
real PresenceTracker, BirdCountTracker, FalconStateMachine, and
EventSignificanceFilter; mocked detector and recording components.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

import numpy as np

from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.detection.detect import Detection
from kanyo.detection.event_types import FalconEvent

FRAME_H = 240
FRAME_W = 320

# Adult-sized box establishing the presence region (ENTER evidence).
ADULT = (100, 100, 140, 140)
# Chick-sized boxes inside/near the region (region = ADULT + 25% margin).
CHICK_A = (108, 110, 122, 124)
CHICK_A_SHIFTED = (116, 110, 130, 124)  # the same chick, wriggled
CHICK_B = (126, 118, 140, 132)
# A second adult perching beside the nest scrape.
ADULT_2 = (60, 96, 100, 136)

T0 = datetime(2026, 7, 16, 12, 0, 0)

EXIT_TIMEOUT = 90
CONFIRMATION_SECONDS = 4
CONFIRMATION_RATIO = 0.5
COUNT_WINDOW = 10


def ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_frame(*blobs: tuple[int, int, int, int]) -> np.ndarray:
    frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
    for x1, y1, x2, y2 in blobs:
        frame[y1:y2, x1:x2] = 200
    return frame


def det(
    confidence: float,
    when: datetime,
    bbox: tuple[int, int, int, int],
    class_id: int = 14,
    class_name: str = "bird",
) -> Detection:
    return Detection(
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        bbox=bbox,
        timestamp=when,
    )


def make_monitor() -> BufferMonitor:
    monitor = BufferMonitor(
        stream_url="test",
        exit_timeout_seconds=EXIT_TIMEOUT,
        presence_enabled=True,
        bird_count_enabled=True,
        bird_count_confirmation_seconds=COUNT_WINDOW,
        full_config={
            "arrival_confirmation_seconds": CONFIRMATION_SECONDS,
            "arrival_confirmation_ratio": CONFIRMATION_RATIO,
        },
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


def spy_events(monitor: BufferMonitor) -> list[FalconEvent]:
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
    raw = filtered if raw is None else raw
    monitor.detector.detect_with_raw.return_value = (filtered, raw)
    monitor.detector.detect_birds.return_value = filtered
    monitor.process_frame(frame, 0, when)


def count_change_calls(monitor: BufferMonitor) -> list:
    return [
        call
        for call in monitor.event_handler.handle_event.call_args_list
        if call.args[0] == FalconEvent.COUNT_CHANGED
    ]


def establish_presence(monitor: BufferMonitor) -> float:
    """Adult ENTER + arrival confirmation on static frames. Returns offset."""
    frame = make_frame(ADULT)
    t = 0.0
    drive(monitor, frame, ts(t), [det(0.8, ts(t), ADULT)])
    while monitor.arrival_pending:
        t += 1.0
        drive(monitor, frame, ts(t), [det(0.8, ts(t), ADULT)])
    return t


def chick_poll_pattern(t: float, when: datetime) -> tuple[np.ndarray, list[Detection]]:
    """One synthetic chick-nest poll: low-conf boxes appear/disappear and the
    chick wriggles (frame content changes), cycling through four phases."""
    phase = int(t) % 8
    if phase < 2:
        # One chick recognized at low confidence, parked.
        return make_frame(CHICK_A), [det(0.22, when, CHICK_A)]
    if phase < 4:
        # Chick wriggles; recognition drops out entirely (motion-only poll).
        return make_frame(CHICK_A_SHIFTED), []
    if phase < 6:
        # Two chicks pop up at low confidence.
        return make_frame(CHICK_A, CHICK_B), [
            det(0.25, when, CHICK_A),
            det(0.18, when, CHICK_B),
        ]
    # Fully quiet poll: chicks huddled, nothing detected, nothing moves.
    return make_frame(CHICK_A, CHICK_B), []


class TestNoExitChurn:
    """The always-occupied nest must never manufacture departures (issue #1
    behavior, verified synthetically per issue #2)."""

    def test_flickering_chick_boxes_hold_presence(self):
        monitor = make_monitor()
        events = spy_events(monitor)
        t = establish_presence(monitor)
        assert events == [FalconEvent.ARRIVED]

        # 6 minutes (4× exit_timeout) of chick-nest polls: low-conf boxes
        # appearing/disappearing, wriggle motion, quiet huddles. No filtered
        # (full-confidence) detection the whole stretch.
        end = t + EXIT_TIMEOUT * 4
        while t < end:
            t += 2.0
            frame, raw = chick_poll_pattern(t, ts(t))
            drive(monitor, frame, ts(t), [], raw)

        assert FalconEvent.DEPARTED not in events
        assert FalconEvent.ARRIVED not in events[1:]  # no re-arrival churn
        assert monitor.state_machine.state.value == "visiting"

    def test_motion_burst_recovered_by_chick_box_does_not_depart(self):
        """A wriggle that reads as an exit candidate (motion then quiet) is
        recovered by the next low-conf chick box before exit_timeout."""
        monitor = make_monitor()
        events = spy_events(monitor)
        t = establish_presence(monitor)

        for _ in range(20):
            # Motion-only poll (chick shifts), then two quiet polls — the
            # tracker starts reporting absent (exit candidate) ...
            t += 2.0
            drive(monitor, make_frame(CHICK_A_SHIFTED), ts(t), [], [])
            t += 2.0
            drive(monitor, make_frame(CHICK_A_SHIFTED), ts(t), [], [])
            t += 2.0
            drive(monitor, make_frame(CHICK_A_SHIFTED), ts(t), [], [])
            # ... but a chick box overlapping the region flips it back well
            # inside exit_timeout: absence never accumulates to a DEPARTED.
            t += 2.0
            drive(
                monitor,
                make_frame(CHICK_A_SHIFTED),
                ts(t),
                [],
                [det(0.2, ts(t), CHICK_A_SHIFTED)],
            )

        assert FalconEvent.DEPARTED not in events
        assert monitor.state_machine.state.value == "visiting"


class TestSaneCounting:
    """Count flicker never confirms; sustained changes confirm exactly once."""

    def _chick_baseline(self, monitor: BufferMonitor) -> float:
        """Establish presence, then a sustained 2-chick baseline count."""
        t = establish_presence(monitor)
        frame = make_frame(CHICK_A, CHICK_B)
        end = t + COUNT_WINDOW + 2
        while t < end:
            t += 1.0
            drive(
                monitor,
                frame,
                ts(t),
                [],
                [det(0.25, ts(t), CHICK_A), det(0.2, ts(t), CHICK_B)],
            )
        assert monitor.bird_count is not None
        assert monitor.bird_count.confirmed_count == 2
        return t

    def test_flickering_chick_count_never_confirms_changes(self):
        """Boxes alternating 2/1/2/1 poll-to-poll (chicks huddling and
        separating) must not move the confirmed count."""
        monitor = make_monitor()
        t = self._chick_baseline(monitor)
        changes_at_baseline = len(count_change_calls(monitor))

        for i in range(120):
            t += 1.0
            if i % 2 == 0:
                raw = [det(0.25, ts(t), CHICK_A)]  # huddled: one box
            else:
                raw = [det(0.25, ts(t), CHICK_A), det(0.2, ts(t), CHICK_B)]
            drive(monitor, make_frame(CHICK_A, CHICK_B), ts(t), [], raw)

        assert monitor.bird_count.confirmed_count == 2
        assert len(count_change_calls(monitor)) == changes_at_baseline

    def test_adult_arrival_and_departure_over_chick_baseline(self):
        """The issue #1 core scenario: chicks hold the baseline; a sustained
        adult raises the count once; the adult leaving lowers it once —
        while ARRIVED/DEPARTED never fire (nest never empties)."""
        monitor = make_monitor()
        events = spy_events(monitor)
        t = self._chick_baseline(monitor)
        baseline_changes = len(count_change_calls(monitor))

        # Adult lands: full-confidence box + the two chicks, sustained.
        frame = make_frame(ADULT_2, CHICK_A, CHICK_B)
        end = t + COUNT_WINDOW + 2
        while t < end:
            t += 1.0
            adult = det(0.85, ts(t), ADULT_2)
            chicks = [det(0.25, ts(t), CHICK_A), det(0.2, ts(t), CHICK_B)]
            drive(monitor, frame, ts(t), [adult], [adult, *chicks])

        assert monitor.bird_count.confirmed_count == 3
        arrival_changes = count_change_calls(monitor)[baseline_changes:]
        assert len(arrival_changes) == 1
        assert arrival_changes[0].args[2] == {"old_count": 2, "new_count": 3}

        # Adult leaves: back to the two chicks, sustained.
        frame = make_frame(CHICK_A, CHICK_B)
        end = t + COUNT_WINDOW + 2
        while t < end:
            t += 1.0
            drive(
                monitor,
                frame,
                ts(t),
                [],
                [det(0.25, ts(t), CHICK_A), det(0.2, ts(t), CHICK_B)],
            )

        assert monitor.bird_count.confirmed_count == 2
        departure_changes = count_change_calls(monitor)[baseline_changes + 1 :]
        assert len(departure_changes) == 1
        assert departure_changes[0].args[2] == {"old_count": 3, "new_count": 2}

        # The nest never emptied: one ARRIVED at establishment, no DEPARTED.
        assert events.count(FalconEvent.ARRIVED) == 1
        assert FalconEvent.DEPARTED not in events
        assert monitor._visit_max_concurrent == 3

    def test_misclassified_chicks_still_counted(self):
        """Chick-sized boxes that YOLO calls something else entirely (the
        at-lens 'elephant' failure mode) still carry the count — any-class
        sustain evidence, not species recognition."""
        monitor = make_monitor()
        t = establish_presence(monitor)

        frame = make_frame(CHICK_A, CHICK_B)
        end = t + COUNT_WINDOW + 2
        while t < end:
            t += 1.0
            raw = [
                det(0.3, ts(t), CHICK_A, class_id=20, class_name="elephant"),
                det(0.19, ts(t), CHICK_B, class_id=0, class_name="person"),
            ]
            drive(monitor, frame, ts(t), [], raw)

        assert monitor.bird_count is not None
        assert monitor.bird_count.confirmed_count == 2
        assert monitor.state_machine.state.value == "visiting"
