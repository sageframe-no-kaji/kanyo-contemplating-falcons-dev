"""Tests for frame.timestamp as the single time authority (ho-11 / 023-B).

Every frame-derived timestamp — buffer adds, detector polls, state machine
updates, confirmation windows, last_detection_time — comes from the frame's
read-time stamp, never from get_now_tz() at processing time. Frames that
queued during a stall carry the times they were actually read: burst-stamping
is structurally impossible.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kanyo.detection.buffer_monitor import BufferMonitor

T0 = datetime(2026, 7, 16, 12, 0, 0)


def make_monitor(**kwargs):
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
        monitor = BufferMonitor(stream_url="test", **kwargs)
    _quiet_frame_mocks(monitor)
    return monitor


def _quiet_frame_mocks(monitor) -> None:
    """Configure recorder/buffer mocks so process_frame runs the poll path."""
    monitor.visit_recorder.is_recording = False
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor.frame_buffer.add_frame.return_value = None
    monitor.state_machine.update.return_value = []
    monitor.detector.detect_birds.return_value = []


def detection(conf: float = 0.8):
    return SimpleNamespace(confidence=conf)


def make_frame():
    frame = MagicMock()
    frame.shape = (720, 1280, 3)
    return frame


class TestProcessFrameTimeAuthority:
    """process_frame derives every timestamp from the passed frame stamp."""

    def test_buffer_add_uses_frame_timestamp(self):
        monitor = make_monitor()
        frame = make_frame()

        monitor.process_frame(frame, frame_number=7, timestamp=T0)

        monitor.frame_buffer.add_frame.assert_called_once_with(frame, T0, 7)

    def test_detector_poll_uses_frame_timestamp(self):
        monitor = make_monitor()
        frame = make_frame()

        monitor.process_frame(frame, frame_number=1, timestamp=T0)

        monitor.detector.detect_birds.assert_called_once_with(frame, timestamp=T0)

    def test_state_machine_update_uses_frame_timestamp(self):
        monitor = make_monitor()

        monitor.process_frame(make_frame(), frame_number=1, timestamp=T0)

        monitor.state_machine.update.assert_called_once_with(False, T0)

    def test_last_detection_time_is_frame_timestamp(self):
        monitor = make_monitor()
        monitor.detector.detect_birds.return_value = [detection(0.9)]

        monitor.process_frame(make_frame(), frame_number=1, timestamp=T0)

        assert monitor.last_detection_time == T0

    def test_no_processing_time_stamping_in_per_frame_path(self):
        """The per-frame data path never re-stamps with get_now_tz()."""
        monitor = make_monitor()
        monitor.detector.detect_birds.return_value = [detection(0.9)]

        with patch("kanyo.detection.buffer_monitor.get_now_tz") as mock_now:
            monitor.process_frame(make_frame(), frame_number=1, timestamp=T0)

        mock_now.assert_not_called()

    def test_stall_then_drain_keeps_read_time_spacing(self):
        """Frames stamped 1s apart at read time, consumed in a tight burst:
        buffer and state machine see monotonic ~1s spacing, not a run of
        near-identical processing-time stamps."""
        monitor = make_monitor()
        stamps = [T0 + timedelta(seconds=i) for i in range(5)]

        for n, stamp in enumerate(stamps):
            monitor.process_frame(make_frame(), frame_number=n, timestamp=stamp)

        buffer_stamps = [c.args[1] for c in monitor.frame_buffer.add_frame.call_args_list]
        update_stamps = [c.args[1] for c in monitor.state_machine.update.call_args_list]
        assert buffer_stamps == stamps
        assert update_stamps == stamps


class TestConfirmationWindowsTimeAuthority:
    """Confirmation windows measure and stamp with frame read times."""

    def test_recovery_confirmation_uses_frame_timestamps(self):
        """A detected frame past the recovery window confirms with the
        frame's stamp as both confirmation time and latest detection (021-J
        semantics preserved, driven by read time)."""
        monitor = make_monitor(stream_recovery_confirmation=10)
        monitor.detector.detect_birds.return_value = [detection(0.9)]
        monitor.recovery_pending = True
        monitor.recovery_pending_start = T0

        ts = T0 + timedelta(seconds=11)
        monitor.process_frame(make_frame(), frame_number=1, timestamp=ts)

        monitor.state_machine.confirm_recovery_presence.assert_called_once_with(
            ts, latest_detection_time=ts
        )
        assert monitor.recovery_pending is False

    def test_startup_confirmation_uses_frame_timestamps(self):
        """Startup presence confirms at the driving frame's read time."""
        monitor = make_monitor()
        monitor.detector.detect_birds.return_value = [detection(0.9)]
        monitor.startup_pending = True
        monitor.startup_pending_start = T0
        monitor.startup_detection_count = 5
        monitor.startup_frame_count = 5

        ts = T0 + timedelta(seconds=monitor.arrival_confirmation_seconds + 1)
        monitor.process_frame(make_frame(), frame_number=1, timestamp=ts)

        monitor.state_machine.confirm_startup_presence.assert_called_once_with(ts)
        assert monitor.startup_pending is False

    def test_cancel_recovery_uses_frame_timestamp(self):
        """A failed recovery cancels through the state machine with the
        driving frame's read time."""
        monitor = make_monitor(stream_recovery_confirmation=10)
        monitor.detector.detect_birds.return_value = []
        monitor.state_machine.cancel_recovery.return_value = []
        monitor.recovery_pending = True
        monitor.recovery_pending_start = T0

        ts = T0 + timedelta(seconds=11)
        monitor.process_frame(make_frame(), frame_number=1, timestamp=ts)

        monitor.state_machine.cancel_recovery.assert_called_once_with(ts)
        assert monitor.recovery_pending is False
