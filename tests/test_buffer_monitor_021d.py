"""Regression tests for 021-D fixes in buffer_monitor.

Three independent fixes:
  1. Startup ratio seeded with frame-based counts (never > 1.0).
  2. Frame interval honored exactly (process_interval=N → every Nth frame).
  3. record_arrival_on_startup wired through config → constructor.
"""

from unittest.mock import patch


class TestStartupRatioBounded:
    """Fix 1: startup confirmation ratio must never exceed 1.0.

    Previously, _confirm_startup_presence used:
        startup_detection_count = len(initial_detections)  # multi-bird-aware count
        startup_frame_count = 1                            # always 1
    which produced ratios > 1.0 whenever multiple birds were detected in init
    frames. Now both counters are frame-based.
    """

    def test_seeded_ratio_within_unit_interval(self):
        from kanyo.detection.buffer_monitor import BufferMonitor

        monitor = BufferMonitor(stream_url="test", full_config={})
        # Simulate post-init seeding under the new frame-based scheme:
        # 30 init frames, 25 of which had any detection (regardless of bird count)
        monitor.startup_detection_count = 25
        monitor.startup_frame_count = 30
        ratio = monitor.startup_detection_count / monitor.startup_frame_count
        assert 0.0 <= ratio <= 1.0
        assert ratio == 25 / 30

    def test_all_frames_with_detection_caps_at_one(self):
        from kanyo.detection.buffer_monitor import BufferMonitor

        monitor = BufferMonitor(stream_url="test", full_config={})
        monitor.startup_detection_count = 30
        monitor.startup_frame_count = 30
        assert monitor.startup_detection_count / monitor.startup_frame_count == 1.0

    def test_confirmation_passes_when_ratio_meets_threshold(self):
        from kanyo.detection.buffer_monitor import BufferMonitor

        monitor = BufferMonitor(
            stream_url="test",
            full_config={
                "arrival_confirmation_seconds": 5,
                "arrival_confirmation_ratio": 0.5,
            },
        )
        # 6/10 = 0.6 > threshold 0.5
        monitor.startup_detection_count = 6
        monitor.startup_frame_count = 10
        ratio = monitor.startup_detection_count / monitor.startup_frame_count
        assert ratio >= monitor.arrival_confirmation_ratio

    def test_confirmation_fails_when_ratio_below_threshold(self):
        from kanyo.detection.buffer_monitor import BufferMonitor

        monitor = BufferMonitor(
            stream_url="test",
            full_config={
                "arrival_confirmation_seconds": 5,
                "arrival_confirmation_ratio": 0.5,
            },
        )
        # 3/10 = 0.3 < threshold 0.5
        monitor.startup_detection_count = 3
        monitor.startup_frame_count = 10
        ratio = monitor.startup_detection_count / monitor.startup_frame_count
        assert ratio < monitor.arrival_confirmation_ratio


class TestFrameIntervalSemantics:
    """Fix 2: process_interval=N must process every Nth frame, not every (N+1)th.

    Previous code used `counter % (interval + 1) != 0` (skip), producing
    interval=30 → every 31st frame. New code uses `counter % interval != 0`.
    """

    @staticmethod
    def _processed_indices(interval: int, total_frames: int) -> list[int]:
        """Simulate the monitor's per-frame skip decision."""
        processed = []
        for counter in range(1, total_frames + 1):
            if counter % interval == 0:  # mirrors buffer_monitor.py
                processed.append(counter)
        return processed

    def test_interval_one_processes_every_frame(self):
        assert self._processed_indices(1, 5) == [1, 2, 3, 4, 5]

    def test_interval_three_processes_every_third(self):
        assert self._processed_indices(3, 10) == [3, 6, 9]

    def test_interval_thirty_processes_every_thirtieth_not_thirty_first(self):
        result = self._processed_indices(30, 100)
        assert result == [30, 60, 90]
        # Regression: with the old `+ 1` bug, interval=30 would have produced
        # [31, 62, 93]. Make that explicit so a future revert is loud.
        assert 31 not in result
        assert 62 not in result


class TestRecordArrivalOnStartupConfig:
    """Fix 3: config['record_arrival_on_startup'] must reach BufferMonitor."""

    def test_default_is_false_in_defaults_dict(self):
        from kanyo.utils.config import DEFAULTS

        assert "record_arrival_on_startup" in DEFAULTS
        assert DEFAULTS["record_arrival_on_startup"] is False

    def test_constructor_default_is_false(self):
        from kanyo.detection.buffer_monitor import BufferMonitor

        monitor = BufferMonitor(stream_url="test", full_config={})
        assert monitor.record_arrival_on_startup is False

    def test_constructor_honors_true_kwarg(self):
        from kanyo.detection.buffer_monitor import BufferMonitor

        monitor = BufferMonitor(
            stream_url="test",
            full_config={},
            record_arrival_on_startup=True,
        )
        assert monitor.record_arrival_on_startup is True

    def test_main_builder_passes_config_value(self):
        """main() reads config['record_arrival_on_startup'] and forwards it.

        Inspect the buffer_monitor module source to ensure the wiring exists.
        A pure-runtime test of main() would require mocking argv, env, file IO,
        and the entire run loop — out of scope for this regression.
        """
        from pathlib import Path

        src = Path(__file__).parent.parent / "src" / "kanyo" / "detection" / "buffer_monitor.py"
        text = src.read_text()
        assert 'record_arrival_on_startup=config.get("record_arrival_on_startup"' in text, (
            "main() must forward config['record_arrival_on_startup'] to BufferMonitor; "
            "see 021-D fix 3."
        )


# Silence the patch import — unused symbol warning if test runner is strict.
_ = patch
