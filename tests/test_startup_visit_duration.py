"""
Test that startup-confirmed birds have correct visit duration on departure.

Regression test for bug where startup-confirmed falcon departures showed "0s visit"
because visit_start was not set during confirm_startup_presence().
"""

from datetime import datetime, timedelta

from kanyo.detection.event_types import FalconEvent, FalconState
from kanyo.detection.falcon_state import FalconStateMachine


__all__ = ["FalconEvent"]  # Used in assertions


def test_startup_confirmation_sets_visit_start():
    """Test that confirm_startup_presence preserves visit_start."""
    config = {}
    fsm = FalconStateMachine(config)
    timestamp = datetime.now()

    # Initialize with falcon present (PENDING_STARTUP)
    fsm.initialize_state(falcon_detected=True, timestamp=timestamp)
    assert fsm.state == FalconState.PENDING_STARTUP
    assert fsm.visit_start == timestamp

    # Confirm startup presence
    confirmation_time = timestamp + timedelta(seconds=10)
    fsm.confirm_startup_presence(confirmation_time)

    # visit_start should still be set (from initialization)
    assert fsm.state == FalconState.ROOSTING
    assert fsm.roosting_start == confirmation_time
    assert fsm.visit_start == timestamp


def test_departure_after_startup_has_nonzero_duration():
    """Test that departure after startup confirmation has correct duration, not 0s."""
    config = {"exit_timeout": 90}
    fsm = FalconStateMachine(config)
    t0 = datetime.now()

    # Initialize with falcon present
    fsm.initialize_state(falcon_detected=True, timestamp=t0)

    # Confirm startup after 10 seconds
    t1 = t0 + timedelta(seconds=10)
    fsm.confirm_startup_presence(t1)
    assert fsm.state == FalconState.ROOSTING

    # Continue detecting for 3 hours
    t2 = t0 + timedelta(hours=3)
    fsm.update(falcon_detected=True, timestamp=t2)

    # Bird leaves - start absence tracking
    t3 = t2 + timedelta(seconds=10)
    fsm.update(falcon_detected=False, timestamp=t3)
    t4 = t2 + timedelta(seconds=100)  # 100s > 90s exit timeout
    events = fsm.update(falcon_detected=False, timestamp=t4)

    # Should have departed with correct duration
    assert len(events) == 1
    assert events[0][0] == FalconEvent.DEPARTED

    metadata = events[0][2]
    # Duration should be from visit_start (t0) to last detection (t2) = 3 hours
    expected_duration = (t2 - t0).total_seconds()
    assert metadata["visit_duration_seconds"] == expected_duration
    assert expected_duration > 0, "Visit duration should not be 0 seconds!"
    assert expected_duration == 10800.0  # 3 hours in seconds
