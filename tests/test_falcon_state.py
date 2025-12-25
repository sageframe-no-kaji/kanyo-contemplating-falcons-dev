"""
Tests for falcon state machine.

Tests state transitions, event generation, and timing logic.
"""

from datetime import datetime, timedelta

import pytest

from kanyo.detection.event_types import FalconEvent, FalconState
from kanyo.detection.falcon_state import FalconStateMachine


class TestFalconStateMachineInitialization:
    """Test state machine initialization and configuration."""

    def test_default_initialization(self):
        """Test state machine initializes to ABSENT with default config."""
        config = {}
        fsm = FalconStateMachine(config)

        assert fsm.state == FalconState.ABSENT
        assert fsm.initializing is True
        assert fsm.visit_start is None
        assert fsm.last_detection is None
        assert fsm.last_absence_start is None
        assert fsm.roosting_start is None
        assert fsm.activity_periods == []
        assert fsm.current_activity_start is None

    def test_custom_config(self):
        """Test configuration values are loaded correctly."""
        config = {
            "exit_timeout": 100,
            "roosting_threshold": 500,
            "roosting_exit_timeout": 200,
            "activity_timeout": 50,
        }
        fsm = FalconStateMachine(config)

        assert fsm.exit_timeout == 100
        assert fsm.roosting_threshold == 500
        assert fsm.roosting_exit_timeout == 200
        assert fsm.activity_timeout == 50

    def test_default_timeouts(self):
        """Test default timeout values when not configured."""
        config = {}
        fsm = FalconStateMachine(config)

        assert fsm.exit_timeout == 300
        assert fsm.roosting_threshold == 1800
        assert fsm.roosting_exit_timeout == 600
        assert fsm.activity_timeout == 180


class TestInitializeState:
    """Test state initialization after startup."""

    def test_initialize_with_falcon_present(self):
        """Test initialization directly to ROOSTING when falcon detected."""
        config = {}
        fsm = FalconStateMachine(config)
        timestamp = datetime.now()

        fsm.initialize_state(falcon_detected=True, timestamp=timestamp)

        assert fsm.state == FalconState.ROOSTING
        assert fsm.initializing is False
        assert fsm.visit_start == timestamp
        assert fsm.last_detection == timestamp
        assert fsm.roosting_start == timestamp

    def test_initialize_with_falcon_absent(self):
        """Test initialization to ABSENT when no falcon detected."""
        config = {}
        fsm = FalconStateMachine(config)
        timestamp = datetime.now()

        fsm.initialize_state(falcon_detected=False, timestamp=timestamp)

        assert fsm.state == FalconState.ABSENT
        assert fsm.initializing is False
        assert fsm.visit_start is None
        assert fsm.last_detection is None


class TestAbsentToVisiting:
    """Test transition from ABSENT to VISITING state."""

    def test_first_detection_during_initialization(self):
        """Test first detection during initialization doesn't trigger ARRIVED event."""
        config = {}
        fsm = FalconStateMachine(config)
        timestamp = datetime.now()

        # Should not generate ARRIVED event during initialization
        events = fsm.update(falcon_detected=True, timestamp=timestamp)

        assert fsm.state == FalconState.VISITING
        assert len(events) == 0  # No event during initialization

    def test_first_detection_after_initialization(self):
        """Test first detection after initialization triggers ARRIVED event."""
        config = {}
        fsm = FalconStateMachine(config)
        timestamp = datetime.now()

        # Initialize to ABSENT
        fsm.initialize_state(falcon_detected=False, timestamp=timestamp)

        # Detection should now trigger ARRIVED
        detection_time = timestamp + timedelta(seconds=10)
        events = fsm.update(falcon_detected=True, timestamp=detection_time)

        assert fsm.state == FalconState.VISITING
        assert len(events) == 1
        assert events[0][0] == FalconEvent.ARRIVED
        assert events[0][1] == detection_time
        assert events[0][2]["visit_start"] == detection_time
        assert fsm.visit_start == detection_time
        assert fsm.last_detection == detection_time


class TestVisitingToRoosting:
    """Test transition from VISITING to ROOSTING state."""

    def test_transition_to_roosting(self):
        """Test transition to ROOSTING after exceeding threshold."""
        config = {"roosting_threshold": 100}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Initialize
        fsm.initialize_state(falcon_detected=False, timestamp=start_time)

        # First detection
        fsm.update(falcon_detected=True, timestamp=start_time)

        # Continue detection for 90 seconds - still visiting
        events = fsm.update(falcon_detected=True, timestamp=start_time + timedelta(seconds=90))
        assert fsm.state == FalconState.VISITING
        assert len(events) == 0

        # Continue detection past threshold - transition to roosting
        roosting_time = start_time + timedelta(seconds=100)
        events = fsm.update(falcon_detected=True, timestamp=roosting_time)

        assert fsm.state == FalconState.ROOSTING
        assert len(events) == 1
        assert events[0][0] == FalconEvent.ROOSTING
        assert events[0][2]["visit_duration"] == 100
        assert fsm.roosting_start == roosting_time


class TestVisitingToDeparted:
    """Test transition from VISITING to ABSENT (departed)."""

    def test_short_visit_then_departure(self):
        """Test falcon departs during short visit."""
        config = {"exit_timeout": 50, "roosting_threshold": 1000}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Initialize and arrive
        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)

        # Detect for 30 seconds
        last_detection = start_time + timedelta(seconds=30)
        fsm.update(falcon_detected=True, timestamp=last_detection)

        # Absence for 40 seconds - not yet departed
        # First absence call sets last_absence_start
        events = fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=1))
        assert fsm.state == FalconState.VISITING
        assert len(events) == 0

        # Continue absence for 40 seconds - still not departed
        events = fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=40))
        assert fsm.state == FalconState.VISITING
        assert len(events) == 0

        # Absence exceeds exit_timeout - departed (51 seconds from first absence)
        events = fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=52))

        assert fsm.state == FalconState.ABSENT
        assert len(events) == 1
        assert events[0][0] == FalconEvent.DEPARTED
        assert events[0][1] == last_detection  # Event timestamp is last detection
        assert events[0][2]["visit_duration"] == 30
        assert fsm.visit_start is None


class TestRoostingToActivity:
    """Test transition from ROOSTING to ACTIVITY state."""

    def test_brief_absence_triggers_activity(self):
        """Test brief absence during roosting triggers ACTIVITY state."""
        config = {"roosting_threshold": 100, "activity_timeout": 50, "roosting_exit_timeout": 200}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Initialize and transition to roosting
        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time + timedelta(seconds=100))

        assert fsm.state == FalconState.ROOSTING

        # Last detection
        last_detection = start_time + timedelta(seconds=120)
        fsm.update(falcon_detected=True, timestamp=last_detection)

        # First absence call sets last_absence_start
        events = fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=1))
        assert fsm.state == FalconState.ROOSTING
        assert len(events) == 0

        # Brief absence for 40 seconds - not yet activity
        events = fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=40))
        assert fsm.state == FalconState.ROOSTING
        assert len(events) == 0

        # Absence exceeds activity_timeout - transition to ACTIVITY
        activity_start = last_detection + timedelta(seconds=51)
        events = fsm.update(falcon_detected=False, timestamp=activity_start)

        assert fsm.state == FalconState.ACTIVITY
        assert len(events) == 1
        assert events[0][0] == FalconEvent.ACTIVITY_START
        assert fsm.current_activity_start is not None


class TestActivityToRoosting:
    """Test transition from ACTIVITY back to ROOSTING."""

    def test_activity_ends_with_detection(self):
        """Test detection during ACTIVITY transitions back to ROOSTING."""
        config = {"roosting_threshold": 100, "activity_timeout": 50}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Set up roosting state
        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time + timedelta(seconds=100))

        # Trigger activity - first absence sets last_absence_start
        last_detection = start_time + timedelta(seconds=120)
        fsm.update(falcon_detected=True, timestamp=last_detection)
        fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=1))
        activity_start = last_detection + timedelta(seconds=51)
        fsm.update(falcon_detected=False, timestamp=activity_start)

        assert fsm.state == FalconState.ACTIVITY
        assert len(fsm.activity_periods) == 0

        # Detection resumes - end activity (duration from last_detection + 1 to activity_end)
        activity_end = activity_start + timedelta(seconds=30)
        events = fsm.update(falcon_detected=True, timestamp=activity_end)

        assert fsm.state == FalconState.ROOSTING
        assert len(events) == 1
        assert events[0][0] == FalconEvent.ACTIVITY_END
        # Activity duration is from last_absence_start (last_detection + 1) to activity_end
        assert events[0][2]["activity_duration"] == (activity_end - (last_detection + timedelta(seconds=1))).total_seconds()
        assert len(fsm.activity_periods) == 1


class TestRoostingToDeparted:
    """Test transition from ROOSTING to ABSENT (departed)."""

    def test_long_absence_during_roosting(self):
        """Test long absence during roosting triggers departure."""
        config = {"roosting_threshold": 100, "activity_timeout": 50, "roosting_exit_timeout": 150}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Set up roosting
        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)
        roosting_time = start_time + timedelta(seconds=100)
        fsm.update(falcon_detected=True, timestamp=roosting_time)

        assert fsm.state == FalconState.ROOSTING

        # Last detection
        last_detection = roosting_time + timedelta(seconds=50)
        fsm.update(falcon_detected=True, timestamp=last_detection)

        # First absence sets last_absence_start
        fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=1))

        # Absence exceeds roosting_exit_timeout - departed
        events = fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=151))

        assert fsm.state == FalconState.ABSENT
        assert len(events) == 1
        assert events[0][0] == FalconEvent.DEPARTED
        assert events[0][1] == last_detection
        assert events[0][2]["roosting_duration"] == 50
        assert fsm.visit_start is None


class TestActivityToDeparted:
    """Test transition from ACTIVITY to ABSENT (departed)."""

    def test_activity_becomes_full_departure(self):
        """Test activity period that exceeds roosting_exit_timeout becomes departure."""
        config = {"roosting_threshold": 100, "activity_timeout": 50, "roosting_exit_timeout": 150}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Set up roosting then activity
        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time + timedelta(seconds=100))
        last_detection = start_time + timedelta(seconds=120)
        fsm.update(falcon_detected=True, timestamp=last_detection)
        # First absence sets last_absence_start
        fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=1))
        activity_start = last_detection + timedelta(seconds=51)
        fsm.update(falcon_detected=False, timestamp=activity_start)

        assert fsm.state == FalconState.ACTIVITY

        # Continue absence past roosting_exit_timeout from current_activity_start - departed
        events = fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=152))

        assert fsm.state == FalconState.ABSENT
        assert len(events) == 1
        assert events[0][0] == FalconEvent.DEPARTED
        assert fsm.visit_start is None


class TestMultipleActivityPeriods:
    """Test tracking multiple activity periods during roosting."""

    def test_multiple_activity_periods(self):
        """Test multiple activity periods are recorded."""
        config = {"roosting_threshold": 100, "activity_timeout": 30}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Set up roosting
        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time + timedelta(seconds=100))

        # First activity period - need to exceed activity_timeout
        last_detection = start_time + timedelta(seconds=120)
        fsm.update(falcon_detected=True, timestamp=last_detection)
        fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=1))
        fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=31))  # Trigger activity
        fsm.update(falcon_detected=True, timestamp=last_detection + timedelta(seconds=60))

        assert len(fsm.activity_periods) == 1

        # Second activity period
        last_detection2 = last_detection + timedelta(seconds=100)
        fsm.update(falcon_detected=True, timestamp=last_detection2)
        fsm.update(falcon_detected=False, timestamp=last_detection2 + timedelta(seconds=1))
        fsm.update(falcon_detected=False, timestamp=last_detection2 + timedelta(seconds=31))  # Trigger activity
        events = fsm.update(falcon_detected=True, timestamp=last_detection2 + timedelta(seconds=50))

        assert len(fsm.activity_periods) == 2
        assert events[-1][0] == FalconEvent.ACTIVITY_END
        assert events[-1][2]["total_activity_periods"] == 2


class TestGetStateInfo:
    """Test state information reporting."""

    def test_state_info_absent(self):
        """Test state info when ABSENT."""
        config = {}
        fsm = FalconStateMachine(config)
        timestamp = datetime.now()

        fsm.initialize_state(falcon_detected=False, timestamp=timestamp)
        info = fsm.get_state_info()

        assert info["state"] == "absent"
        assert info["visit_start"] is None
        assert info["last_detection"] is None
        assert info["activity_periods"] == 0

    def test_state_info_visiting(self):
        """Test state info during VISITING."""
        config = {}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        visit_time = start_time + timedelta(seconds=10)
        fsm.update(falcon_detected=True, timestamp=visit_time)

        current_time = visit_time + timedelta(seconds=30)
        fsm.update(falcon_detected=True, timestamp=current_time)

        info = fsm.get_state_info()

        assert info["state"] == "visiting"
        assert info["visit_start"] == visit_time.isoformat()
        assert info["current_visit_duration"] == 30

    def test_state_info_roosting(self):
        """Test state info during ROOSTING."""
        config = {"roosting_threshold": 100}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        fsm.initialize_state(falcon_detected=True, timestamp=start_time)
        info = fsm.get_state_info()

        assert info["state"] == "roosting"
        assert info["roosting_start"] == start_time.isoformat()
        assert info["roosting_duration"] == 0

    def test_state_info_with_absence(self):
        """Test state info includes absence duration."""
        config = {"exit_timeout": 100}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Visit then absence
        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)
        last_detection = start_time + timedelta(seconds=20)
        fsm.update(falcon_detected=True, timestamp=last_detection)

        # First absence sets last_absence_start to this timestamp
        current_time = last_detection + timedelta(seconds=30)
        fsm.update(falcon_detected=False, timestamp=current_time)

        # Check 10 seconds later - duration is 10 from current_time (when absence started)
        info = fsm.get_state_info(current_time=current_time + timedelta(seconds=10))

        assert info["current_absence_duration"] == 10


class TestComplexScenario:
    """Test complex scenarios with multiple state transitions."""

    def test_full_visit_cycle(self):
        """Test a complete visit cycle: ABSENT → VISITING → ROOSTING → ACTIVITY → ROOSTING → ABSENT."""
        config = {
            "exit_timeout": 50,
            "roosting_threshold": 100,
            "roosting_exit_timeout": 150,
            "activity_timeout": 30,
        }
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # Start ABSENT
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        assert fsm.state == FalconState.ABSENT

        # ABSENT → VISITING
        t1 = t0 + timedelta(seconds=10)
        events = fsm.update(falcon_detected=True, timestamp=t1)
        assert fsm.state == FalconState.VISITING
        assert events[0][0] == FalconEvent.ARRIVED

        # Continue VISITING
        t2 = t1 + timedelta(seconds=50)
        fsm.update(falcon_detected=True, timestamp=t2)
        assert fsm.state == FalconState.VISITING

        # VISITING → ROOSTING
        t3 = t1 + timedelta(seconds=100)
        events = fsm.update(falcon_detected=True, timestamp=t3)
        assert fsm.state == FalconState.ROOSTING
        assert events[0][0] == FalconEvent.ROOSTING

        # Continue ROOSTING
        t4 = t3 + timedelta(seconds=50)
        fsm.update(falcon_detected=True, timestamp=t4)

        # ROOSTING → ACTIVITY
        # First absence sets last_absence_start
        fsm.update(falcon_detected=False, timestamp=t4 + timedelta(seconds=1))
        t5 = t4 + timedelta(seconds=31)
        events = fsm.update(falcon_detected=False, timestamp=t5)
        assert fsm.state == FalconState.ACTIVITY
        assert events[0][0] == FalconEvent.ACTIVITY_START

        # ACTIVITY → ROOSTING
        t6 = t5 + timedelta(seconds=20)
        events = fsm.update(falcon_detected=True, timestamp=t6)
        assert fsm.state == FalconState.ROOSTING
        assert events[0][0] == FalconEvent.ACTIVITY_END

        # ROOSTING → ABSENT - first absence sets last_absence_start
        fsm.update(falcon_detected=False, timestamp=t6 + timedelta(seconds=1))
        t7 = t6 + timedelta(seconds=151)
        events = fsm.update(falcon_detected=False, timestamp=t7)
        assert fsm.state == FalconState.ABSENT
        assert events[0][0] == FalconEvent.DEPARTED


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_rapid_detection_changes(self):
        """Test rapid on/off detection patterns."""
        config = {"exit_timeout": 100, "roosting_threshold": 200}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)

        # Rapid changes should track last detection
        for i in range(10):
            t = start_time + timedelta(seconds=i * 5)
            detected = i % 2 == 0
            fsm.update(falcon_detected=detected, timestamp=t)

        # Should still be in VISITING (not enough time for roosting)
        assert fsm.state == FalconState.VISITING

    def test_exact_threshold_boundary(self):
        """Test state transition at exact threshold boundary."""
        config = {"roosting_threshold": 100}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        fsm.initialize_state(falcon_detected=False, timestamp=start_time)
        fsm.update(falcon_detected=True, timestamp=start_time)

        # Exactly at threshold should trigger transition
        events = fsm.update(falcon_detected=True, timestamp=start_time + timedelta(seconds=100))

        assert fsm.state == FalconState.ROOSTING
        assert events[0][0] == FalconEvent.ROOSTING
        assert events[0][2]["visit_duration"] == 100

    def test_continuous_detection_updates_last_detection(self):
        """Test continuous detections update last_detection timestamp."""
        config = {}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        fsm.initialize_state(falcon_detected=True, timestamp=start_time)

        # Multiple detections should update last_detection
        t1 = start_time + timedelta(seconds=10)
        fsm.update(falcon_detected=True, timestamp=t1)
        assert fsm.last_detection == t1

        t2 = start_time + timedelta(seconds=20)
        fsm.update(falcon_detected=True, timestamp=t2)
        assert fsm.last_detection == t2
