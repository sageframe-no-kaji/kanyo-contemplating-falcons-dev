"""Unit tests for BirdCountTracker (issue #3).

Pure-module tests: all time is injected, no pipeline imports. The tracker's
contract is the 017 spec's confirmed-vs-suspected distinction rebuilt on the
presence layer's evidence discipline: count changes confirm only after
sustained differing evidence, evidence gaps hold, zero crossings are silent.
"""

from datetime import datetime, timedelta

from kanyo.detection.bird_count import BirdCountTracker, CountChange

T0 = datetime(2026, 7, 16, 12, 0, 0)


def ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class TestInitialState:
    def test_starts_at_zero(self):
        tracker = BirdCountTracker()
        assert tracker.confirmed_count == 0
        assert tracker.max_confirmed == 0

    def test_default_confirmation_window(self):
        assert BirdCountTracker().confirmation_seconds == 10.0

    def test_custom_confirmation_window(self):
        assert BirdCountTracker(confirmation_seconds=30).confirmation_seconds == 30


class TestSustainedConfirmation:
    def test_change_confirms_after_window(self):
        tracker = BirdCountTracker(confirmation_seconds=10)
        assert tracker.update(1, ts(0)) is None  # suspicion opens
        assert tracker.update(1, ts(5)) is None  # window still running
        change = tracker.update(1, ts(10))  # exactly the window: confirms
        assert isinstance(change, CountChange)
        assert (change.old_count, change.new_count) == (0, 1)
        assert change.timestamp == ts(10)
        assert change.suspected_since == ts(0)
        assert tracker.confirmed_count == 1

    def test_single_frame_flicker_never_confirms(self):
        """A second box appearing for one poll must not change the count."""
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(1, ts(0))
        tracker.update(1, ts(10))  # confirmed at 1
        assert tracker.update(2, ts(11)) is None  # flicker up
        assert tracker.update(1, ts(12)) is None  # back — suspicion resets
        # 2 reappears much later: the old suspicion must not have aged.
        assert tracker.update(2, ts(30)) is None
        assert tracker.update(2, ts(35)) is None
        assert tracker.confirmed_count == 1

    def test_oscillation_back_resets_window(self):
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(1, ts(0))
        tracker.update(1, ts(10))  # confirmed at 1
        tracker.update(2, ts(20))  # suspicion opens
        tracker.update(1, ts(25))  # fluctuates back — reset
        tracker.update(2, ts(26))  # suspicion reopens
        assert tracker.update(2, ts(35)) is None  # only 9s since reopen
        change = tracker.update(2, ts(36))
        assert change is not None
        assert (change.old_count, change.new_count) == (1, 2)

    def test_changed_suspicion_restarts_window(self):
        """2 then 3 while confirmed at 1: the window restarts on 3."""
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(1, ts(0))
        tracker.update(1, ts(10))  # confirmed at 1
        tracker.update(2, ts(20))  # suspect 2
        tracker.update(3, ts(25))  # suspect 3 — restart
        assert tracker.update(3, ts(34)) is None  # 9s on the 3-window
        change = tracker.update(3, ts(35))
        assert change is not None
        assert (change.old_count, change.new_count) == (1, 3)

    def test_decrease_confirms_symmetrically(self):
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(2, ts(0))
        tracker.update(2, ts(10))  # confirmed at 2
        tracker.update(1, ts(20))
        change = tracker.update(1, ts(30))
        assert change is not None
        assert (change.old_count, change.new_count) == (2, 1)


class TestEvidenceGaps:
    def test_none_holds_confirmed_count(self):
        """Parked bird: no boxes at any threshold is no count evidence."""
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(1, ts(0))
        tracker.update(1, ts(10))  # confirmed at 1
        for t in range(11, 400, 5):
            assert tracker.update(None, ts(t)) is None
        assert tracker.confirmed_count == 1

    def test_none_holds_open_suspicion(self):
        """An evidence gap neither confirms nor refutes a suspected change."""
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(1, ts(0))
        tracker.update(1, ts(10))  # confirmed at 1
        tracker.update(2, ts(20))  # suspicion opens
        tracker.update(None, ts(25))  # gap — window survives
        change = tracker.update(2, ts(30))  # 10s since ts(20): confirms
        assert change is not None
        assert change.suspected_since == ts(20)


class TestResetAndMax:
    def test_reset_zeroes_without_emitting(self):
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(2, ts(0))
        tracker.update(2, ts(10))
        assert tracker.confirmed_count == 2
        tracker.reset()
        assert tracker.confirmed_count == 0
        assert tracker.max_confirmed == 0

    def test_max_confirmed_is_running_max(self):
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(3, ts(0))
        tracker.update(3, ts(10))  # confirmed 3
        tracker.update(1, ts(20))
        tracker.update(1, ts(30))  # confirmed 1
        assert tracker.confirmed_count == 1
        assert tracker.max_confirmed == 3

    def test_state_info_snapshot(self):
        tracker = BirdCountTracker(confirmation_seconds=10)
        tracker.update(1, ts(0))
        tracker.update(1, ts(10))
        tracker.update(2, ts(20))
        info = tracker.state_info()
        assert info["confirmed_count"] == 1
        assert info["max_confirmed"] == 1
        assert info["suspected_count"] == 2
        assert info["suspected_since"] == ts(20).isoformat()
        assert info["confirmation_seconds"] == 10

    def test_state_info_no_suspicion(self):
        info = BirdCountTracker().state_info()
        assert info["suspected_count"] is None
        assert info["suspected_since"] is None
