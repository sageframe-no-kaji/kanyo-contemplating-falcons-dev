"""
Falcon event and state type definitions.

Enumerations for tracking falcon behavior states and transition events.
"""

from enum import Enum


class FalconEvent(Enum):
    """Events that occur during falcon behavior state transitions."""

    ARRIVED = "arrived"  # Falcon entered after absence
    DEPARTED = "departed"  # Falcon left (exceeded timeout)
    ROOSTING = "roosting"  # Transitioned to long-term presence
    ACTIVITY_START = "activity_start"  # Movement during roost
    ACTIVITY_END = "activity_end"  # Settled during roost


class FalconState(Enum):
    """States representing falcon presence and behavior patterns."""

    ABSENT = "absent"
    VISITING = "visiting"
    ROOSTING = "roosting"
    ACTIVITY = "activity"
