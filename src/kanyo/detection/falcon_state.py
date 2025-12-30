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

    State Transitions:
    - ABSENT → VISITING: Detection after absence (Event: ARRIVED)
    - VISITING → ROOSTING: Continuous presence > threshold (Event: ROOSTING)
    - VISITING → ABSENT: No detection > exit_timeout (Event: DEPARTED)
    - ROOSTING → ABSENT: No detection > exit_timeout (Event: DEPARTED)
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

        if self.state == FalconState.VISITING:
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

    def initialize_state(self, falcon_detected: bool, timestamp: datetime) -> None:
        """
        Initialize state based on startup detection.

        Called after processing initial frames to set correct starting state
        without generating false arrival events.

        Args:
            falcon_detected: Whether falcon was detected in initial frames
            timestamp: Timestamp of initialization
        """
        if falcon_detected:
            # Falcon already present - initialize to ROOSTING state
            # (assume already there if detected at startup)
            self.state = FalconState.ROOSTING
            self.visit_start = timestamp
            self.last_detection = timestamp
            self.roosting_start = timestamp
            logger.info("🏠 Initialized to ROOSTING state (falcon already present)")
        else:
            # No falcon - stay in ABSENT
            logger.info("📭 Initialized to ABSENT state (no falcon detected)")

        # Exit initialization mode
        self.initializing = False

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
