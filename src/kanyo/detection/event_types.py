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


class FalconState(Enum):
    """States representing falcon presence and behavior patterns."""

    ABSENT = "absent"
    VISITING = "visiting"
    ROOSTING = "roosting"  # Same timeout as VISITING, just for notification
    PENDING_STARTUP = "pending_startup"  # Confirming falcon presence at startup/recovery
