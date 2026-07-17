"""
Bird count tracking: how many birds, not just whether (issue #3).

BirdCountTracker sits beside the presence layer, on the same evidence. The
state machine keeps its binary presence semantics untouched — count is a
parallel judgment, never a new state (the 017 spec's core structural rule,
kept). The tracker consumes a per-frame *candidate count* the monitor derives
from the detection views:

- Filtered (full-confidence, target-class) detections are the strongest count
  evidence: candidate = number of filtered boxes.
- Sustain-level raw boxes carry the count through recognition dropouts the
  presence layer already tolerates (an at-lens bird classified "elephant" is
  still one bird): candidate = number of sustain-level boxes when the
  filtered view is empty.
- No boxes at all while presence holds (the parked-bird signature) is NO
  evidence about count — the candidate is ``None`` and the confirmed count
  holds, mirroring the presence layer's "absence of recognition is not
  evidence of absence".

Count changes confirm only after sustained evidence (``confirmation_seconds``
of an unbroken differing candidate — the 017 spec's confirmed-vs-suspected
distinction). A candidate that flickers back to the confirmed count resets
the suspicion window, so per-frame YOLO noise (chick-cam scenarios, issue #1)
never surfaces as a count change.

The module is pure: no pipeline imports, no wall-clock reads. All time comes
from the ``timestamp`` argument, so tests are deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CountChange:
    """One confirmed change of the bird count."""

    old_count: int
    new_count: int
    timestamp: datetime  # when the change confirmed (not when first suspected)
    suspected_since: datetime  # when the differing candidate first appeared


class BirdCountTracker:
    """Sustained-confirmation bird count beside the presence boolean."""

    def __init__(self, confirmation_seconds: float = 10.0) -> None:
        """
        Initialize the tracker.

        Args:
            confirmation_seconds: How long an unbroken differing candidate
                count must persist before the confirmed count changes.
        """
        self.confirmation_seconds = confirmation_seconds

        self._confirmed_count: int = 0
        self._suspected_count: int | None = None
        self._suspected_since: datetime | None = None
        self._max_confirmed: int = 0

    @property
    def confirmed_count(self) -> int:
        """The stable, verified bird count."""
        return self._confirmed_count

    @property
    def max_confirmed(self) -> int:
        """Highest confirmed count since the last reset (visit-scoped max)."""
        return self._max_confirmed

    def reset(self) -> None:
        """Drop to zero without emitting a change (visit over / force-reset).

        The 0-boundary surface belongs to the arrival/departure events; the
        count tracker never announces its own zero crossings.
        """
        self._confirmed_count = 0
        self._suspected_count = None
        self._suspected_since = None
        self._max_confirmed = 0

    def update(self, candidate: int | None, timestamp: datetime) -> CountChange | None:
        """Feed one poll's candidate count; return a confirmed change, if any.

        Args:
            candidate: The per-frame candidate count, or ``None`` when the
                poll carried no count evidence (parked bird: presence holds
                with no boxes at any threshold). ``None`` holds the confirmed
                count AND any open suspicion window — an evidence gap neither
                confirms nor refutes a suspected change.
            timestamp: The frame's read-time timestamp (the time authority).

        Returns:
            A :class:`CountChange` when a differing candidate has persisted
            for ``confirmation_seconds``, else ``None``.
        """
        if candidate is None:
            return None

        if candidate == self._confirmed_count:
            # Count fluctuated back — the suspected change was noise.
            self._suspected_count = None
            self._suspected_since = None
            return None

        if candidate != self._suspected_count or self._suspected_since is None:
            # New (or changed) suspicion: restart the window.
            self._suspected_count = candidate
            self._suspected_since = timestamp
            return None

        if (timestamp - self._suspected_since).total_seconds() >= self.confirmation_seconds:
            change = CountChange(
                old_count=self._confirmed_count,
                new_count=candidate,
                timestamp=timestamp,
                suspected_since=self._suspected_since,
            )
            self._confirmed_count = candidate
            self._max_confirmed = max(self._max_confirmed, candidate)
            self._suspected_count = None
            self._suspected_since = None
            logger.debug(
                f"Bird count confirmed: {change.old_count} → {change.new_count} "
                f"(suspected since {change.suspected_since.isoformat()})"
            )
            return change

        return None

    def state_info(self) -> dict[str, Any]:
        """Diagnostics snapshot for logging."""
        return {
            "confirmed_count": self._confirmed_count,
            "max_confirmed": self._max_confirmed,
            "suspected_count": self._suspected_count,
            "suspected_since": (
                self._suspected_since.isoformat() if self._suspected_since else None
            ),
            "confirmation_seconds": self.confirmation_seconds,
        }
