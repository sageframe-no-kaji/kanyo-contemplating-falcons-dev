"""Tests for 022-E: periodic rolling detection-confidence summary.

One EVENT-level aggregate line per detection_summary_interval seconds with a
stable field order — the data source for tuning detection thresholds.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.utils.logger import EVENT

MONITOR_LOGGER = "kanyo.detection.buffer_monitor"


def make_monitor(detection_summary_interval: int = 300):
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
            detection_summary_interval=detection_summary_interval,
        )
    monitor.visit_recorder.is_recording = False
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor.frame_buffer.add_frame.return_value = None
    monitor.state_machine.update.return_value = []
    return monitor


def detection(conf: float):
    return SimpleNamespace(confidence=conf)


def make_frame():
    frame = MagicMock()
    frame.shape = (720, 1280, 3)
    return frame


def summary_lines(caplog):
    return [r.message for r in caplog.records if "Detection summary" in r.message]


class TestSummaryComputation:
    def test_stats_over_known_sequence(self, caplog):
        """min/median/max/ratio are computed over detected polls only."""
        monitor = make_monitor()
        monitor._summary_poll_count = 4
        monitor._summary_detected_confidences = [0.4, 0.8, 0.6]

        with caplog.at_level(EVENT, logger=MONITOR_LOGGER):
            monitor._emit_detection_summary()

        lines = summary_lines(caplog)
        assert len(lines) == 1
        assert "(300s):" in lines[0]
        assert "polls=4 detected=3 ratio=75.0%" in lines[0]
        assert "conf min=0.40 median=0.60 max=0.80" in lines[0]

    def test_no_detections_emits_na_line(self, caplog):
        """The line still appears with conf=n/a so gaps stay visible."""
        monitor = make_monitor()
        monitor._summary_poll_count = 10
        monitor._summary_detected_confidences = []

        with caplog.at_level(EVENT, logger=MONITOR_LOGGER):
            monitor._emit_detection_summary()

        lines = summary_lines(caplog)
        assert len(lines) == 1
        assert "polls=10 detected=0 ratio=0.0% conf=n/a" in lines[0]

    def test_emission_resets_accumulator(self):
        monitor = make_monitor()
        monitor._summary_poll_count = 4
        monitor._summary_detected_confidences = [0.4]

        monitor._emit_detection_summary()

        assert monitor._summary_poll_count == 0
        assert monitor._summary_detected_confidences == []


class TestSummaryCadence:
    def test_emits_once_per_interval_and_resets_window(self, caplog):
        """One line per elapsed interval; the next window starts fresh."""
        import time as real_time

        monitor = make_monitor(detection_summary_interval=300)
        monitor.detector.detect_birds.side_effect = [
            [detection(0.5)],  # poll 1 — window not elapsed, no emission
            [],  # poll 2 — window forced elapsed, emission
            [detection(0.9)],  # poll 3 — fresh window, no emission
        ]
        now = datetime(2026, 7, 1, 10, 0, 0)
        frame = make_frame()

        with caplog.at_level(EVENT, logger=MONITOR_LOGGER):
            monitor.process_frame(frame, frame_number=0, timestamp=now)
            assert summary_lines(caplog) == []

            # Force the window to have elapsed before the second poll
            monitor._summary_window_start = real_time.time() - 301
            monitor.process_frame(frame, frame_number=1, timestamp=now)

            monitor.process_frame(frame, frame_number=2, timestamp=now)

        lines = summary_lines(caplog)
        assert len(lines) == 1
        assert "polls=2 detected=1 ratio=50.0%" in lines[0]
        assert "conf min=0.50 median=0.50 max=0.50" in lines[0]

        # Third poll landed in the fresh window
        assert monitor._summary_poll_count == 1
        assert monitor._summary_detected_confidences == [0.9]
        # Emission restarted the window at (roughly) emission time
        assert real_time.time() - monitor._summary_window_start < 300

    def test_interval_zero_disables_summary(self, caplog):
        monitor = make_monitor(detection_summary_interval=0)
        monitor.detector.detect_birds.return_value = [detection(0.5)]
        now = datetime(2026, 7, 1, 10, 0, 0)
        frame = make_frame()

        with caplog.at_level(EVENT, logger=MONITOR_LOGGER):
            for n in range(5):
                monitor.process_frame(frame, frame_number=n, timestamp=now)

        assert summary_lines(caplog) == []
        assert monitor._summary_poll_count == 0
        assert monitor._summary_detected_confidences == []


class TestSummaryConfig:
    def test_default_in_config_defaults(self):
        from kanyo.utils.config import DEFAULTS

        assert DEFAULTS["detection_summary_interval"] == 300

    def test_monitor_default_interval(self):
        monitor = make_monitor()
        assert monitor.detection_summary_interval == 300
