"""
Tests for falcon state machine.

Tests state transitions, event generation, and timing logic.
"""

from datetime import datetime, timedelta

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

    def test_custom_config(self):
        """Test configuration values are loaded correctly."""
        config = {
            "exit_timeout": 100,
            "roosting_threshold": 500,
        }
        fsm = FalconStateMachine(config)

        assert fsm.exit_timeout == 100
        assert fsm.roosting_threshold == 500

    def test_default_timeouts(self):
        """Test default timeout values when not configured."""
        config = {}
        fsm = FalconStateMachine(config)

        assert fsm.exit_timeout == 90
        assert fsm.roosting_threshold == 1800


class TestInitializeState:
    """Test state initialization after startup."""

    def test_initialize_with_falcon_present(self):
        """Test initialization to PENDING_STARTUP when falcon detected.

        When falcon is detected on startup, we now use PENDING_STARTUP state
        to require confirmation (like arrival confirmation) before transitioning
        to ROOSTING. This prevents false positive telegram notifications on startup.
        """
        config = {}
        fsm = FalconStateMachine(config)
        timestamp = datetime.now()

        returned_state = fsm.initialize_state(falcon_detected=True, timestamp=timestamp)

        assert returned_state == FalconState.PENDING_STARTUP
        assert fsm.state == FalconState.PENDING_STARTUP
        assert fsm.initializing is False
        assert fsm.visit_start == timestamp
        assert fsm.last_detection == timestamp

    def test_initialize_with_falcon_present_then_confirm(self):
        """Test confirmation from PENDING_STARTUP to ROOSTING."""
        config = {}
        fsm = FalconStateMachine(config)
        timestamp = datetime.now()

        fsm.initialize_state(falcon_detected=True, timestamp=timestamp)
        assert fsm.state == FalconState.PENDING_STARTUP

        # Confirm startup presence
        fsm.confirm_startup_presence(timestamp)

        assert fsm.state == FalconState.ROOSTING
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
        assert events[0][2]["visit_duration_seconds"] == 100
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
        assert events[0][2]["visit_duration_seconds"] == 30
        assert fsm.visit_start is None


class TestRoostingToDeparted:
    """Test transition from ROOSTING to ABSENT (departed)."""

    def test_long_absence_during_roosting(self):
        """Test long absence during roosting triggers departure."""
        config = {"roosting_threshold": 100, "exit_timeout": 50}
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

        # Absence exceeds exit_timeout - departed (same for ROOSTING now)
        events = fsm.update(falcon_detected=False, timestamp=last_detection + timedelta(seconds=51))

        assert fsm.state == FalconState.ABSENT
        assert len(events) == 1
        assert events[0][0] == FalconEvent.DEPARTED
        assert events[0][1] == last_detection
        assert events[0][2]["roosting_duration"] == 50
        assert fsm.visit_start is None


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
        """Test state info during ROOSTING (after confirmation)."""
        config = {"roosting_threshold": 100}
        fsm = FalconStateMachine(config)
        start_time = datetime.now()

        # Initialize starts in PENDING_STARTUP
        fsm.initialize_state(falcon_detected=True, timestamp=start_time)
        # Confirm to transition to ROOSTING
        fsm.confirm_startup_presence(start_time)

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
        """Test a complete visit cycle: ABSENT → VISITING → ROOSTING → ABSENT."""
        config = {
            "exit_timeout": 50,
            "roosting_threshold": 100,
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

        # ROOSTING → ABSENT - first absence sets last_absence_start
        fsm.update(falcon_detected=False, timestamp=t4 + timedelta(seconds=1))
        t5 = t4 + timedelta(seconds=51)
        events = fsm.update(falcon_detected=False, timestamp=t5)
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
        assert events[0][2]["visit_duration_seconds"] == 100

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


class TestOutageCompensation:
    """Test stream outage handling."""

    def test_outage_prevents_false_departure(self):
        """Stream outage should not count toward absence duration."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)  # Bird arrives

        # Bird detected at t0+10
        t1 = t0 + timedelta(seconds=10)
        fsm.update(falcon_detected=True, timestamp=t1)

        # Stream drops for 60s (outage)
        fsm.add_outage(60)

        # First frame after reconnect at t0+70 - no detection
        t2 = t0 + timedelta(seconds=70)
        fsm.update(falcon_detected=False, timestamp=t2)

        # Another frame at t0+100 - no detection
        # Real absence: 90s, but 60s was outage
        # Effective absence: 30s < 90s threshold
        t3 = t0 + timedelta(seconds=100)
        events = fsm.update(falcon_detected=False, timestamp=t3)

        assert fsm.state == FalconState.VISITING
        assert len(events) == 0

    def test_outage_resets_on_detection(self):
        """Outage accumulator should reset when bird detected."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        fsm.initialize_state(falcon_detected=True, timestamp=t0)
        fsm.add_outage(30)
        assert fsm.cumulative_outage == 30

        # Bird detected - should reset outage
        t1 = t0 + timedelta(seconds=10)
        fsm.update(falcon_detected=True, timestamp=t1)

        assert fsm.cumulative_outage == 0.0

    def test_multiple_outages_accumulate(self):
        """Multiple outages should accumulate until detection."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        fsm.initialize_state(falcon_detected=True, timestamp=t0)

        fsm.add_outage(20)
        fsm.add_outage(30)
        fsm.add_outage(10)

        assert fsm.cumulative_outage == 60

    def test_real_departure_still_works(self):
        """Real departure should still trigger after accounting for outages."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)

        # 20s outage
        fsm.add_outage(20)

        # Start absence
        t1 = t0 + timedelta(seconds=10)
        fsm.update(falcon_detected=False, timestamp=t1)

        # 130s later - real absence is 120s, minus 20s outage = 100s effective
        # 100s > 90s threshold = should depart
        t2 = t0 + timedelta(seconds=130)
        events = fsm.update(falcon_detected=False, timestamp=t2)

        assert fsm.state == FalconState.ABSENT
        assert len(events) == 1
        assert events[0][0] == FalconEvent.DEPARTED


class TestStreamRecovery:
    """Test stream recovery confirmation flow."""

    def test_set_pending_recovery_from_visiting(self):
        """Setting PENDING_RECOVERY preserves visit context."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # Arrive and visit
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)
        assert fsm.state == FalconState.VISITING
        assert fsm.visit_start == t0

        # Stream outage - set pending recovery
        t1 = t0 + timedelta(seconds=30)
        fsm.set_pending_recovery(t1)

        assert fsm.state == FalconState.PENDING_RECOVERY
        assert fsm.pre_outage_state == FalconState.VISITING
        assert fsm.visit_start == t0  # Visit start preserved

    def test_set_pending_recovery_from_roosting(self):
        """Setting PENDING_RECOVERY from ROOSTING preserves roosting context."""
        config = {"exit_timeout": 90, "roosting_threshold": 60}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # Arrive and transition to roosting
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)

        # Transition to roosting
        t1 = t0 + timedelta(seconds=61)
        fsm.update(falcon_detected=True, timestamp=t1)
        assert fsm.state == FalconState.ROOSTING
        roosting_start = fsm.roosting_start

        # Stream outage - set pending recovery
        t2 = t0 + timedelta(seconds=90)
        fsm.set_pending_recovery(t2)

        assert fsm.state == FalconState.PENDING_RECOVERY
        assert fsm.pre_outage_state == FalconState.ROOSTING
        assert fsm.pre_outage_roosting_start == roosting_start

    def test_confirm_recovery_restores_visiting(self):
        """Confirming recovery restores VISITING state."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # Setup: visiting - outage - pending recovery
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)
        t1 = t0 + timedelta(seconds=30)
        fsm.set_pending_recovery(t1)

        # Confirm recovery
        t2 = t0 + timedelta(seconds=40)
        fsm.confirm_recovery_presence(t2)

        assert fsm.state == FalconState.VISITING
        assert fsm.pre_outage_state is None
        assert fsm.last_detection == t2

    def test_confirm_recovery_restores_roosting(self):
        """Confirming recovery restores ROOSTING state with roosting_start."""
        config = {"exit_timeout": 90, "roosting_threshold": 60}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # Setup: roosting - outage - pending recovery
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)
        t1 = t0 + timedelta(seconds=61)
        fsm.update(falcon_detected=True, timestamp=t1)
        original_roosting_start = fsm.roosting_start

        t2 = t0 + timedelta(seconds=90)
        fsm.set_pending_recovery(t2)

        # Confirm recovery
        t3 = t0 + timedelta(seconds=100)
        fsm.confirm_recovery_presence(t3)

        assert fsm.state == FalconState.ROOSTING
        assert fsm.roosting_start == original_roosting_start
        assert fsm.pre_outage_state is None

    def test_cancel_recovery_generates_departure(self):
        """Canceling recovery generates DEPARTED event with correct timing."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # Setup: visiting with last detection
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)

        # Update detection time
        t1 = t0 + timedelta(seconds=30)
        fsm.update(falcon_detected=True, timestamp=t1)

        # Stream outage - set pending recovery
        t2 = t0 + timedelta(seconds=40)
        fsm.set_pending_recovery(t2)

        # Cancel recovery - bird left
        t3 = t0 + timedelta(seconds=50)
        events = fsm.cancel_recovery(t3)

        assert fsm.state == FalconState.ABSENT
        assert len(events) == 1
        event_type, event_time, metadata = events[0]
        assert event_type == FalconEvent.DEPARTED
        assert event_time == t1  # Last detection time, not current time
        assert metadata["visit_start"] == t0
        assert metadata["visit_end"] == t1
        assert metadata.get("departed_during_outage") is True

    def test_is_falcon_present(self):
        """is_falcon_present returns True for presence states."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # ABSENT - not present
        assert not fsm.is_falcon_present()

        # VISITING - present
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)
        assert fsm.is_falcon_present()

        # PENDING_RECOVERY - present
        fsm.set_pending_recovery(t0)
        assert fsm.is_falcon_present()

    def test_detection_during_recovery_updates_last_detection(self):
        """Detection during PENDING_RECOVERY updates last_detection time."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # Setup: visiting - outage - pending recovery
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)
        t1 = t0 + timedelta(seconds=30)
        fsm.set_pending_recovery(t1)

        # Detection during recovery confirmation
        t2 = t0 + timedelta(seconds=35)
        events = fsm.update(falcon_detected=True, timestamp=t2)

        assert fsm.state == FalconState.PENDING_RECOVERY
        assert fsm.last_detection == t2
        assert len(events) == 0  # No events generated during confirmation

    def test_absence_during_recovery_handled(self):
        """Absence during PENDING_RECOVERY is handled gracefully."""
        config = {"exit_timeout": 90}
        fsm = FalconStateMachine(config)
        t0 = datetime.now()

        # Setup: visiting - outage - pending recovery
        fsm.initialize_state(falcon_detected=False, timestamp=t0)
        fsm.update(falcon_detected=True, timestamp=t0)
        t1 = t0 + timedelta(seconds=30)
        fsm.set_pending_recovery(t1)

        # No detection (absence) during recovery confirmation
        t2 = t0 + timedelta(seconds=35)
        events = fsm.update(falcon_detected=False, timestamp=t2)

        # Should stay in PENDING_RECOVERY - buffer_monitor handles confirmation
        assert fsm.state == FalconState.PENDING_RECOVERY
        assert len(events) == 0
