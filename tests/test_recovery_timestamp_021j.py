"""Regression tests for 021-J: confirm_recovery_presence must record the
actual latest detection time, not the confirmation-window end time.

Previously, after a successful recovery, FalconStateMachine.last_detection
was set to the confirmation moment — inflating visit duration and skewing
departure clip end frames whenever the bird's actual last detection during
the confirmation window was earlier than the confirmation moment.
"""

from datetime import datetime, timedelta

from kanyo.detection.event_types import FalconEvent, FalconState
from kanyo.detection.falcon_state import FalconStateMachine


def _make_pending_recovery() -> FalconStateMachine:
    """Construct an FSM that is mid-PENDING_RECOVERY after a VISITING outage."""
    fsm = FalconStateMachine({"exit_timeout": 90, "roosting_threshold": 1800})
    fsm.initializing = False
    # Manually drive into VISITING (bypassing initialization gate)
    visit_start = datetime(2026, 1, 15, 10, 0, 0)
    fsm.state = FalconState.VISITING
    fsm.visit_start = visit_start
    fsm.last_detection = visit_start
    # Then into PENDING_RECOVERY (e.g. via set_pending_recovery)
    outage_time = visit_start + timedelta(minutes=5)
    fsm.set_pending_recovery(outage_time)
    return fsm


class TestConfirmRecoveryRespectsLatestDetectionTime:
    def test_uses_latest_detection_time_when_provided(self):
        fsm = _make_pending_recovery()
        # Bird's actual last frame was 3 seconds before confirmation moment
        latest_real_detection = datetime(2026, 1, 15, 10, 5, 7)
        confirmation_moment = datetime(2026, 1, 15, 10, 5, 10)

        fsm.confirm_recovery_presence(
            confirmation_moment, latest_detection_time=latest_real_detection
        )

        # last_detection must equal the REAL detection time, not the confirmation moment
        assert fsm.last_detection == latest_real_detection
        # Sanity: state restored
        assert fsm.state == FalconState.VISITING

    def test_falls_back_to_confirmation_time_when_no_detection_passed(self):
        """Safety fallback: if caller doesn't pass latest_detection_time, the
        confirmation timestamp is used. This preserves the old behavior for
        any caller not yet updated to 021-J."""
        fsm = _make_pending_recovery()
        confirmation_moment = datetime(2026, 1, 15, 10, 5, 10)

        fsm.confirm_recovery_presence(confirmation_moment)

        assert fsm.last_detection == confirmation_moment
        assert fsm.state == FalconState.VISITING

    def test_explicit_none_falls_back_to_confirmation_time(self):
        fsm = _make_pending_recovery()
        confirmation_moment = datetime(2026, 1, 15, 10, 5, 10)

        fsm.confirm_recovery_presence(confirmation_moment, latest_detection_time=None)

        assert fsm.last_detection == confirmation_moment

    def test_no_effect_when_not_in_pending_recovery(self):
        """Calling confirm_recovery_presence from a wrong state must be a no-op."""
        fsm = FalconStateMachine({"exit_timeout": 90, "roosting_threshold": 1800})
        assert fsm.state == FalconState.ABSENT
        # Pre-set last_detection to verify it's not overwritten
        pre_existing = datetime(2026, 1, 15, 9, 0, 0)
        fsm.last_detection = pre_existing

        fsm.confirm_recovery_presence(
            datetime(2026, 1, 15, 10, 0, 0),
            latest_detection_time=datetime(2026, 1, 15, 9, 59, 59),
        )

        # State stays ABSENT, last_detection NOT changed
        assert fsm.state == FalconState.ABSENT
        assert fsm.last_detection == pre_existing


class TestVisitDurationNotInflated:
    """End-to-end: visit duration measured off last_detection is not
    artificially extended by the confirmation window."""

    def test_visit_duration_reflects_real_last_detection(self):
        fsm = _make_pending_recovery()
        # Visit started at 10:00:00, real last detection at 10:05:07
        # Confirmation moment is at 10:05:10 (3 seconds later)
        real_last = datetime(2026, 1, 15, 10, 5, 7)
        confirm_at = datetime(2026, 1, 15, 10, 5, 10)

        fsm.confirm_recovery_presence(confirm_at, latest_detection_time=real_last)

        # Duration computed off (last_detection - visit_start) should be 5:07,
        # NOT 5:10 (which it would be under the old bug).
        assert fsm.visit_start is not None
        assert fsm.last_detection is not None
        duration = (fsm.last_detection - fsm.visit_start).total_seconds()
        assert duration == 307.0  # 5 minutes 7 seconds, not 310
        # Old bug would give 310.0 — pin the difference
        assert duration < 310.0


class TestBufferMonitorPassesLatestDetectionTime:
    """Source-level check that _confirm_recovery passes recovery_latest_detection
    to state_machine.confirm_recovery_presence."""

    def test_confirm_recovery_passes_latest_detection_kwarg(self):
        from pathlib import Path

        src = Path(__file__).parent.parent / "src" / "kanyo" / "detection" / "buffer_monitor.py"
        text = src.read_text()
        # Find the _confirm_recovery method body
        idx_method = text.find("def _confirm_recovery(")
        assert idx_method != -1, "_confirm_recovery method not found"
        # Look in the next ~1000 chars
        body = text[idx_method : idx_method + 1500]
        assert "latest_detection_time=" in body, (
            "_confirm_recovery must pass latest_detection_time= to "
            "state_machine.confirm_recovery_presence (021-J)"
        )
        assert (
            "recovery_latest_detection" in body
        ), "_confirm_recovery must reference recovery_latest_detection (021-J)"


# silence unused import warnings if any
_ = FalconEvent
