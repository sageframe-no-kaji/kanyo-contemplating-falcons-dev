"""Tests for no-frame sentinel consumption in the monitor (ho-11 / 023-B).

The sentinel from capture is the only outage signal. While a visit is
recording it finally engages visit_recorder.write_frame(None) — freeze-frame
fill and stream_outage_exceeded accounting — and outage stretches key
state_machine.add_outage and recovery-confirmation entry off sentinel data
instead of a wall-clock gap heuristic that was blind to blocked reads.
"""

import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

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
    return monitor


def make_frame(frame_number: int, timestamp: datetime):
    """Minimal stand-in for a capture Frame with a read-time stamp."""
    return SimpleNamespace(
        data=np.zeros((4, 4, 3), dtype=np.uint8),
        frame_number=frame_number,
        timestamp=timestamp,
    )


class TestSentinelHandling:
    """_handle_no_frame_sentinel: the per-sentinel consumption path."""

    def test_sentinel_writes_freeze_frame_while_recording(self):
        """An active recording receives write_frame(None) per sentinel."""
        monitor = make_monitor()
        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stream_outage_exceeded = False

        monitor._handle_no_frame_sentinel()

        monitor.visit_recorder.write_frame.assert_called_once_with(None)
        assert monitor._outage_start is not None
        assert monitor._outage_sentinel_count == 1

    def test_sentinel_without_recording_leaves_recorder_untouched(self):
        """No recording: sentinels must not crash and must not touch the recorder."""
        monitor = make_monitor()
        monitor.visit_recorder.is_recording = False

        monitor._handle_no_frame_sentinel()
        monitor._handle_no_frame_sentinel()

        monitor.visit_recorder.write_frame.assert_not_called()
        monitor.visit_recorder.stop_recording.assert_not_called()
        assert monitor._outage_sentinel_count == 2

    def test_outage_exceeded_stops_recording_and_resets_state(self):
        """stream_outage_exceeded runs the existing stop/reset path."""
        monitor = make_monitor()
        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stream_outage_exceeded = True
        monitor.arrival_pending = True
        monitor._visit_peak_confidence = 0.9

        monitor._handle_no_frame_sentinel()

        monitor.visit_recorder.write_frame.assert_called_once_with(None)
        monitor.visit_recorder.stop_recording.assert_called_once()
        monitor.arrival_clip_recorder.stop_recording.assert_called_once()
        monitor.state_machine.reset_to_absent.assert_called_once()
        # Pending confirmations and the visit peak are discarded
        assert monitor.arrival_pending is False
        assert monitor._visit_peak_confidence == 0.0

    def test_outage_start_fixed_across_sentinel_stretch(self):
        """The stretch keeps its first-sentinel time; only the count grows."""
        monitor = make_monitor()
        monitor.visit_recorder.is_recording = False

        monitor._handle_no_frame_sentinel()
        first_start = monitor._outage_start
        monitor._handle_no_frame_sentinel()
        monitor._handle_no_frame_sentinel()

        assert monitor._outage_start is first_start
        assert monitor._outage_sentinel_count == 3


class TestOutageRecovery:
    """_handle_outage_recovery: first real frame after a sentinel stretch."""

    def _in_outage(self, monitor, last_frame_at: datetime, sentinels: int = 2):
        monitor._last_frame_timestamp = last_frame_at
        monitor._outage_start = last_frame_at + timedelta(seconds=10)
        monitor._outage_sentinel_count = sentinels

    def test_short_outage_with_bird_enters_recovery_confirmation(self):
        """Bird present + outage within threshold + recording -> PENDING_RECOVERY."""
        monitor = make_monitor(stream_recovery_threshold=30)
        self._in_outage(monitor, T0)
        monitor.state_machine.is_falcon_present.return_value = True
        monitor.visit_recorder.is_recording = True

        now = T0 + timedelta(seconds=20)
        monitor._handle_outage_recovery(now)

        monitor.state_machine.add_outage.assert_called_once_with(20.0)
        monitor.state_machine.set_pending_recovery.assert_called_once_with(now)
        assert monitor.recovery_pending is True
        assert monitor.recovery_pending_start == now
        assert monitor.recovery_detection_count == 0
        assert monitor.recovery_latest_detection is None
        # Outage stretch is consumed
        assert monitor._outage_start is None
        assert monitor._outage_sentinel_count == 0

    def test_long_outage_accounts_but_does_not_enter_recovery(self):
        """Outage past the threshold: add_outage only, no recovery window."""
        monitor = make_monitor(stream_recovery_threshold=30)
        self._in_outage(monitor, T0, sentinels=12)
        monitor.state_machine.is_falcon_present.return_value = True
        monitor.visit_recorder.is_recording = True

        monitor._handle_outage_recovery(T0 + timedelta(seconds=120))

        monitor.state_machine.add_outage.assert_called_once_with(120.0)
        monitor.state_machine.set_pending_recovery.assert_not_called()
        assert monitor.recovery_pending is False

    def test_no_recording_means_no_recovery_window(self):
        """Recovery confirmation requires an active recording, as before."""
        monitor = make_monitor(stream_recovery_threshold=30)
        self._in_outage(monitor, T0)
        monitor.state_machine.is_falcon_present.return_value = True
        monitor.visit_recorder.is_recording = False

        monitor._handle_outage_recovery(T0 + timedelta(seconds=15))

        monitor.state_machine.add_outage.assert_called_once_with(15.0)
        monitor.state_machine.set_pending_recovery.assert_not_called()
        assert monitor.recovery_pending is False

    def test_startup_outage_duration_falls_back_to_sentinel_time(self):
        """With no frame ever seen, duration estimates from the first
        sentinel plus the read timeout."""
        monitor = make_monitor(stream_read_timeout_s=10.0)
        monitor._last_frame_timestamp = None
        monitor._outage_start = T0
        monitor._outage_sentinel_count = 1
        monitor.state_machine.is_falcon_present.return_value = False

        monitor._handle_outage_recovery(T0 + timedelta(seconds=15))

        monitor.state_machine.add_outage.assert_called_once_with(25.0)


class TestRunLoopSentinelWiring:
    """run() routes sentinels and post-outage frames to the handlers."""

    def _runnable_monitor(self):
        monitor = make_monitor(stream_recovery_threshold=30)
        # Shutdown path needs an unpackable stop_recording result
        monitor.visit_recorder.stop_recording.return_value = (None, None)
        monitor.arrival_clip_recorder.is_recording.return_value = False
        monitor.state_machine.update.return_value = []
        return monitor

    def test_frames_sentinels_frames_drives_outage_and_recovery(self):
        """frames -> sentinels -> frame: freeze-frame fill per sentinel while
        recording, then outage accounting + recovery entry on the first real
        frame (021-F's integration case)."""
        monitor = self._runnable_monitor()
        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stream_outage_exceeded = False
        monitor.state_machine.is_falcon_present.return_value = True

        f1 = make_frame(1, T0)
        f2 = make_frame(2, T0 + timedelta(seconds=20))
        monitor.capture.frames.return_value = iter([f1, None, None, f2])

        with (
            patch("kanyo.detection.buffer_monitor.signal"),
            patch("kanyo.detection.buffer_monitor.time") as mock_time,
        ):
            # First frame arrives past the 30s init window so initialization
            # completes immediately (empty init detections -> ABSENT-style
            # init; the mocked state machine absorbs it).
            mock_time.time.side_effect = itertools.count(0, 31)
            mock_time.sleep = MagicMock()
            monitor.run()

        # Each sentinel fed the freeze-frame path of the active recording
        none_writes = [
            c for c in monitor.visit_recorder.write_frame.call_args_list if c.args[0] is None
        ]
        assert len(none_writes) == 2

        # First real frame after the stretch ran outage accounting with the
        # frame-timestamp gap (20s) and entered recovery confirmation
        monitor.state_machine.add_outage.assert_called_once_with(20.0)
        monitor.state_machine.set_pending_recovery.assert_called_once_with(f2.timestamp)
        assert monitor.recovery_pending is True
        assert monitor.recovery_pending_start == f2.timestamp

    def test_sentinels_without_recording_do_not_crash_run(self):
        """Sentinel-only outage with no active recording: run() survives and
        the recorder is never fed."""
        monitor = self._runnable_monitor()
        monitor.visit_recorder.is_recording = False
        monitor.state_machine.is_falcon_present.return_value = False

        f1 = make_frame(1, T0)
        f2 = make_frame(2, T0 + timedelta(seconds=45))
        monitor.capture.frames.return_value = iter([f1, None, None, None, f2])

        with (
            patch("kanyo.detection.buffer_monitor.signal"),
            patch("kanyo.detection.buffer_monitor.time") as mock_time,
        ):
            mock_time.time.side_effect = itertools.count(0, 31)
            mock_time.sleep = MagicMock()
            monitor.run()

        monitor.visit_recorder.write_frame.assert_not_called()
        monitor.state_machine.add_outage.assert_called_once_with(45.0)
        monitor.state_machine.set_pending_recovery.assert_not_called()
        assert monitor.recovery_pending is False
