"""
Falcon state machine for intelligent behavior tracking.

Distinguishes between visits and roosting periods.
Roosting is mainly for notifications - both states use the same exit timeout.
"""

from __future__ import annotations

from datetime import datetime

from kanyo.utils.logger import get_logger

from .event_types import FalconEvent, FalconState

logger = get_logger(__name__)


class FalconStateMachine:
    """
    State machine for tracking falcon presence and behavior.

    States:
    - ABSENT: No falcon detected
    - VISITING: Falcon present (< roosting threshold)
    - ROOSTING: Long-term presence (> roosting threshold, notification only)
    - PENDING_STARTUP: Confirming falcon presence at startup
    - PENDING_RECOVERY: Confirming falcon presence after stream recovery

    State Transitions:
    - ABSENT → VISITING: Detection after absence (Event: ARRIVED)
    - VISITING → ROOSTING: Continuous presence > threshold (Event: ROOSTING)
    - VISITING → ABSENT: No detection > exit_timeout (Event: DEPARTED)
    - ROOSTING → ABSENT: No detection > exit_timeout (Event: DEPARTED)
    - VISITING/ROOSTING → PENDING_RECOVERY: Stream recovered after outage
    - PENDING_RECOVERY → VISITING/ROOSTING: Falcon confirmed still present
    - PENDING_RECOVERY → ABSENT: Falcon left during outage (Event: DEPARTED)
    """

    def __init__(self, config: dict):
        """
        Initialize state machine with timing thresholds.

        Args:
            config: Dictionary containing:
                - exit_timeout: Seconds to wait before departed (default: 90)
                - roosting_threshold: Seconds before transitioning to roosting (default: 1800)
        """
        self.state = FalconState.ABSENT
        self.config = config
        self.initializing = True  # Prevent false arrivals during startup

        # Timing thresholds (in seconds)
        self.exit_timeout = config.get("exit_timeout", 90)
        self.roosting_threshold = config.get("roosting_threshold", 1800)

        # State tracking timestamps
        self.visit_start: datetime | None = None
        self.last_detection: datetime | None = None
        self.last_absence_start: datetime | None = None
        self.roosting_start: datetime | None = None
        self.cumulative_outage = 0.0  # Track total outage time during absence

        # Recovery state tracking - remember state before stream outage
        self.pre_outage_state: FalconState | None = None
        self.pre_outage_roosting_start: datetime | None = None

    def update(
        self, falcon_detected: bool, timestamp: datetime
    ) -> list[tuple[FalconEvent, datetime, dict]]:
        """
        Update state based on detection status.

        Args:
            falcon_detected: Whether falcon is currently detected
            timestamp: Current timestamp

        Returns:
            List of (event_type, event_timestamp, metadata) tuples for events that occurred
        """
        events = []

        if falcon_detected:
            events.extend(self._handle_detection(timestamp))
        else:
            events.extend(self._handle_absence(timestamp))

        return events

    def add_outage(self, seconds: float) -> None:
        """Add stream outage time. This time won't count toward absence duration."""
        self.cumulative_outage += seconds

    def is_falcon_present(self) -> bool:
        """Check if falcon is currently present (in a state where recovery is relevant).

        Returns:
            True if in VISITING, ROOSTING, or any confirmation state
        """
        return self.state in (
            FalconState.VISITING,
            FalconState.ROOSTING,
            FalconState.PENDING_STARTUP,
            FalconState.PENDING_RECOVERY,
        )

    def _handle_detection(self, timestamp: datetime) -> list[tuple[FalconEvent, datetime, dict]]:
        """Handle falcon detection based on current state."""
        events = []

        if self.state == FalconState.ABSENT:
            # ABSENT → VISITING: Falcon arrived
            self.state = FalconState.VISITING
            self.visit_start = timestamp
            self.last_detection = timestamp
            self.last_absence_start = None

            # Only trigger ARRIVED event if not in initialization mode
            if not self.initializing:
                events.append((FalconEvent.ARRIVED, timestamp, {"visit_start": timestamp}))

        elif self.state == FalconState.PENDING_STARTUP:
            # PENDING_STARTUP: Just update detection time
            # Confirmation is handled by buffer_monitor based on detection ratio
            self.last_detection = timestamp
            self.last_absence_start = None

        elif self.state == FalconState.PENDING_RECOVERY:
            # PENDING_RECOVERY: Just update detection time
            # Confirmation is handled by buffer_monitor based on detection ratio
            self.last_detection = timestamp
            self.last_absence_start = None

        elif self.state == FalconState.VISITING:
            # Update detection time
            self.last_detection = timestamp
            self.last_absence_start = None

            # Check if should transition to ROOSTING
            if self.visit_start:
                visit_duration = (timestamp - self.visit_start).total_seconds()
                if visit_duration >= self.roosting_threshold:
                    # VISITING → ROOSTING: Long-term presence
                    self.state = FalconState.ROOSTING
                    self.roosting_start = timestamp

                    metadata: dict[str, datetime | float | None] = {
                        "visit_start": self.visit_start,
                        "visit_duration_seconds": visit_duration,
                        "roosting_start": timestamp,
                    }

                    events.append(
                        (
                            FalconEvent.ROOSTING,
                            timestamp,
                            metadata,
                        )
                    )

        elif self.state == FalconState.ROOSTING:
            # Already roosting, update detection time
            self.last_detection = timestamp
            self.last_absence_start = None

        # Reset outage accumulator on detection
        self.cumulative_outage = 0.0

        return events

    def _handle_absence(self, timestamp: datetime) -> list[tuple[FalconEvent, datetime, dict]]:
        """Handle falcon absence based on current state."""
        events = []

        # Track absence start
        if self.last_absence_start is None and self.last_detection is not None:
            self.last_absence_start = timestamp

        if self.state == FalconState.PENDING_STARTUP:
            # PENDING_STARTUP with absence: Falcon wasn't really there
            # Let buffer_monitor handle confirmation failure based on ratio
            pass

        elif self.state == FalconState.PENDING_RECOVERY:
            # PENDING_RECOVERY with absence: Falcon may have left during outage
            # Let buffer_monitor handle confirmation failure based on ratio
            pass

        elif self.state == FalconState.VISITING:
            # Check if absence exceeds exit timeout
            if self.last_detection and self.last_absence_start:
                absence_duration = (timestamp - self.last_absence_start).total_seconds()
                effective_absence = absence_duration - self.cumulative_outage
                if effective_absence >= self.exit_timeout:
                    # VISITING → ABSENT: Falcon departed
                    visit_duration = (
                        (self.last_detection - self.visit_start).total_seconds()
                        if self.visit_start
                        else 0
                    )

                    events.append(
                        (
                            FalconEvent.DEPARTED,
                            self.last_detection,
                            {
                                "visit_start": self.visit_start,
                                "visit_end": self.last_detection,
                                "visit_duration_seconds": visit_duration,
                                "total_visit_duration": visit_duration,
                            },
                        )
                    )

                    # Reset state
                    self._reset_state()

        elif self.state == FalconState.ROOSTING:
            # Check if absence exceeds exit timeout (same as VISITING)
            if self.last_detection and self.last_absence_start:
                absence_duration = (timestamp - self.last_absence_start).total_seconds()
                effective_absence = absence_duration - self.cumulative_outage

                if effective_absence >= self.exit_timeout:
                    # ROOSTING → ABSENT: Falcon departed
                    total_duration = (
                        (self.last_detection - self.visit_start).total_seconds()
                        if self.visit_start
                        else 0
                    )
                    roosting_duration = (
                        (self.last_detection - self.roosting_start).total_seconds()
                        if self.roosting_start
                        else 0
                    )

                    events.append(
                        (
                            FalconEvent.DEPARTED,
                            self.last_detection,
                            {
                                "visit_start": self.visit_start,
                                "visit_end": self.last_detection,
                                "visit_duration_seconds": total_duration,
                                "roosting_duration": roosting_duration,
                            },
                        )
                    )

                    self._reset_state()

        return events

    def _reset_state(self):
        """Reset state machine to ABSENT."""
        self.state = FalconState.ABSENT
        self.visit_start = None
        self.last_detection = None
        self.last_absence_start = None
        self.roosting_start = None
        self.cumulative_outage = 0.0
        # Reset recovery state
        self.pre_outage_state = None
        self.pre_outage_roosting_start = None

    def reset_to_absent(self) -> None:
        """Force reset to ABSENT state (used when arrival confirmation fails)."""
        self._reset_state()
        logger.info("🔄 State machine reset to ABSENT (arrival not confirmed)")

    def set_pending_startup(self, timestamp: datetime) -> None:
        """
        Set state to PENDING_STARTUP for confirmation after startup or stream recovery.

        Unlike normal arrival, confirmed startup presence does NOT trigger telegram.

        Args:
            timestamp: When falcon was first detected
        """
        self.state = FalconState.PENDING_STARTUP
        self.visit_start = timestamp
        self.last_detection = timestamp
        self.last_absence_start = None
        logger.info("🔄 Set to PENDING_STARTUP - awaiting confirmation...")

    def confirm_startup_presence(self, timestamp: datetime) -> None:
        """
        Confirm falcon presence from startup/recovery.

        Transitions from PENDING_STARTUP to ROOSTING without generating events.
        Recording should have already started.

        Args:
            timestamp: Confirmation time
        """
        if self.state != FalconState.PENDING_STARTUP:
            logger.warning(f"confirm_startup_presence called in {self.state} state, ignoring")
            return

        self.state = FalconState.ROOSTING
        self.roosting_start = timestamp
        self.last_detection = timestamp
        # Set visit_start so departure duration is calculated correctly
        # Use roosting_start as visit start time for startup-confirmed birds
        if not self.visit_start:
            self.visit_start = timestamp
        logger.info("✅ STARTUP PRESENCE CONFIRMED - now ROOSTING (no notification)")

    def set_pending_recovery(self, timestamp: datetime) -> None:
        """
        Set state to PENDING_RECOVERY after stream reconnection.

        Called when stream recovers after an outage while falcon was present.
        Preserves visit context for proper clip creation if falcon departed during outage.

        Args:
            timestamp: When stream reconnected
        """
        # Remember the state we were in before the outage
        if self.state in (FalconState.VISITING, FalconState.ROOSTING):
            self.pre_outage_state = self.state
            self.pre_outage_roosting_start = self.roosting_start
        else:
            self.pre_outage_state = FalconState.VISITING
            self.pre_outage_roosting_start = None

        self.state = FalconState.PENDING_RECOVERY
        self.last_absence_start = None
        logger.info(
            f"🔄 Set to PENDING_RECOVERY (was {self.pre_outage_state.value}) - "
            "awaiting confirmation..."
        )

    def confirm_recovery_presence(self, timestamp: datetime) -> None:
        """
        Confirm falcon still present after stream recovery.

        Transitions from PENDING_RECOVERY back to previous state.
        No notification is sent - falcon never actually left.

        Args:
            timestamp: Confirmation time
        """
        if self.state != FalconState.PENDING_RECOVERY:
            logger.warning(f"confirm_recovery_presence called in {self.state} state, ignoring")
            return

        # Restore to previous state
        if self.pre_outage_state == FalconState.ROOSTING:
            self.state = FalconState.ROOSTING
            self.roosting_start = self.pre_outage_roosting_start
        else:
            self.state = FalconState.VISITING

        self.last_detection = timestamp
        self.last_absence_start = None
        self.pre_outage_state = None
        self.pre_outage_roosting_start = None
        logger.info(f"✅ RECOVERY CONFIRMED - falcon still present, resuming {self.state.value}")

    def cancel_recovery(self, timestamp: datetime) -> list[tuple[FalconEvent, datetime, dict]]:
        """
        Cancel recovery - falcon left during the stream outage.

        Generates DEPARTED event with proper timing using last_detection before outage.

        Args:
            timestamp: Current time (not used for event timestamp)

        Returns:
            List containing DEPARTED event with proper metadata
        """
        if self.state != FalconState.PENDING_RECOVERY:
            logger.warning(f"cancel_recovery called in {self.state} state, ignoring")
            return []

        # Generate departure event with last_detection (from before outage)
        total_duration = (
            (self.last_detection - self.visit_start).total_seconds()
            if self.visit_start and self.last_detection
            else 0
        )
        roosting_duration = (
            (self.last_detection - self.pre_outage_roosting_start).total_seconds()
            if self.pre_outage_roosting_start and self.last_detection
            else 0
        )

        events = [
            (
                FalconEvent.DEPARTED,
                self.last_detection,  # Use last detection time, not current time
                {
                    "visit_start": self.visit_start,
                    "visit_end": self.last_detection,
                    "visit_duration_seconds": total_duration,
                    "roosting_duration": roosting_duration,
                    "departed_during_outage": True,
                },
            )
        ]

        logger.info(
            f"⚠️ RECOVERY CANCELLED - falcon left during outage "
            f"(last seen: {self.last_detection})"
        )

        self._reset_state()
        return events

    def initialize_state(self, falcon_detected: bool, timestamp: datetime) -> FalconState:
        """
        Initialize state based on startup detection.

        Called after processing initial frames to set correct starting state
        without generating false arrival events.

        Args:
            falcon_detected: Whether falcon was detected in initial frames
            timestamp: Timestamp of initialization

        Returns:
            The initial state (for buffer_monitor to know if confirmation needed)
        """
        if falcon_detected:
            # Falcon detected - go to PENDING_STARTUP for confirmation
            # Don't immediately assume ROOSTING - could be a false positive
            self.set_pending_startup(timestamp)
        else:
            # No falcon - stay in ABSENT
            logger.info("📭 Initialized to ABSENT state (no falcon detected)")

        # Exit initialization mode
        self.initializing = False
        return self.state

    def get_state_info(self, current_time: datetime | None = None) -> dict:
        """
        Get current state information.

        Args:
            current_time: Current timestamp for calculating durations (defaults to last_detection)

        Returns:
            Dictionary with current state and relevant timing information
        """
        info: dict[str, str | float | None] = {
            "state": self.state.value,
            "visit_start": self.visit_start.isoformat() if self.visit_start else None,
            "last_detection": self.last_detection.isoformat() if self.last_detection else None,
            "last_absence_start": (
                self.last_absence_start.isoformat() if self.last_absence_start else None
            ),
            "roosting_start": self.roosting_start.isoformat() if self.roosting_start else None,
        }

        # Calculate durations
        if self.visit_start and self.last_detection:
            info["current_visit_duration"] = (
                self.last_detection - self.visit_start
            ).total_seconds()

        if self.roosting_start and self.last_detection:
            info["roosting_duration"] = (self.last_detection - self.roosting_start).total_seconds()

        if self.last_absence_start:
            # Use provided current_time or last_detection
            ref_time = current_time or self.last_detection
            if ref_time:
                info["current_absence_duration"] = (
                    ref_time - self.last_absence_start
                ).total_seconds()

        return info
