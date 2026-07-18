"""
Falcon event and state type definitions.

Enumerations for tracking falcon behavior states and transition events.
"""

from enum import Enum


class FalconEvent(Enum):
    """Events that occur during falcon behavior state transitions."""

    ARRIVED = "arrived"  # Falcon entered after absence
    DEPARTED = "departed"  # Falcon left (exceeded timeout)
    ROOSTING = "roosting"  # Transitioned to long-term presence (notification only)
    STARTUP_CONFIRMED = "startup_confirmed"  # Falcon confirmed at startup (no notify)
    # Confirmed bird-count change while occupied (issue #3). Emitted by the
    # count tracker beside the state machine, never by the state machine
    # itself — count is a parallel judgment, not a state. Notification only;
    # no event-store row (the visit row carries max_concurrent_birds).
    COUNT_CHANGED = "count_changed"


class FalconState(Enum):
    """States representing falcon presence and behavior patterns."""

    ABSENT = "absent"
    VISITING = "visiting"
    ROOSTING = "roosting"  # Same timeout as VISITING, just for notification
    PENDING_STARTUP = "pending_startup"  # Confirming falcon presence at startup/recovery
    PENDING_RECOVERY = "pending_recovery"  # Confirming falcon presence after stream recovery
