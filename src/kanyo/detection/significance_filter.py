"""
Event significance filter (ho-09 / 025-A).

The judgment layer between state-machine events and their surface effects
(notifications, event-store rows). The state machine stays pure — it reports
what happened; this filter decides what it means. Recording mechanics run on
raw events elsewhere; the filter governs only the surface.

Three behaviors:

- **Merge window** — a DEPARTED is held for ``merge_window_seconds``; an
  ARRIVED inside the window is a continuation of the same visit (no new
  notification, continuation arrival clip discarded, one merged row with a
  ``merged_segments`` count). Window expiry releases the departure.
- **Minimum significance** — visits with detection-duration below
  ``min_significant_seconds`` are de-surfaced, never dropped: the row is
  still recorded, flagged ``insignificant``, with no notification.
- **Activity damping** — above ``damping_arrivals_threshold`` pass-through
  arrivals per ``damping_window_hours``, individual notifications are
  suppressed and ``tick`` emits one summary decision per window.

Pure and deterministic: all time comes in as arguments — no wall-clock reads.
The module emits :class:`FilterDecision` objects; it never sends
notifications or writes files itself. Pipeline wiring is 025-B.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kanyo.detection.event_types import FalconEvent
from kanyo.detection.falcon_state import EventMetadata, StateEvent
from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FilterDecision:
    """One surface decision about a state-machine event.

    ``event_type`` is ``None`` only for damping summary pseudo-decisions
    (``is_summary=True``) — the event vocabulary does not grow (ho-09).
    """

    event_type: FalconEvent | None
    event_time: datetime
    metadata: EventMetadata
    notify: bool  # send the public notification?
    record: bool  # append the event-store row now?
    merged_segments: int = 1  # >= 2 when this row spans merged visits
    insignificant: bool = False  # below min_significant_seconds
    discard_arrival_clip: bool = False  # continuation clip to delete (merge case)
    is_summary: bool = False  # damping summary pseudo-decision


class EventSignificanceFilter:
    """Holds, merges, classifies, and releases state-machine events.

    All behaviors are individually disabled by ``0`` values, and the whole
    filter by ``enabled=False`` — pass-through decisions then reproduce
    today's behavior exactly (ho-09 backward-compatibility contract).
    """

    def __init__(
        self,
        merge_window_seconds: float = 300,
        min_significant_seconds: float = 30,
        damping_arrivals_threshold: int = 8,
        damping_window_hours: float = 1,
        enabled: bool = True,
    ):
        self.merge_window_seconds = merge_window_seconds
        self.min_significant_seconds = min_significant_seconds
        self.damping_arrivals_threshold = damping_arrivals_threshold
        self.damping_window_hours = damping_window_hours
        self.enabled = enabled

        # Held DEPARTED awaiting merge-window expiry: (event_time, metadata).
        # The metadata is already merged (visit_start of the chain's first
        # segment) at hold time.
        self._held: tuple[datetime, EventMetadata] | None = None
        # Open merge chain: first segment's visit_start and segment count.
        # A chain opens when an ARRIVED swallows a held DEPARTED and closes
        # when the eventual real departure is released.
        self._chain_visit_start: datetime | None = None
        self._chain_segments: int = 1

        # Damping state: rolling deque of pass-through ARRIVED timestamps
        # and of released visit (timestamp, detection-duration) pairs for
        # the summary median.
        self._arrivals: deque[datetime] = deque()
        self._recent_durations: deque[tuple[datetime, float]] = deque()
        self._damped = False
        self._last_summary_time: datetime | None = None

    # ── public API ────────────────────────────────────────────────────────

    def process(self, event: StateEvent, now: datetime) -> list[FilterDecision]:
        """Feed one state-machine event through the filter.

        Args:
            event: The (event_type, event_time, metadata) tuple.
            now: The caller's driving timestamp (frame read time).

        Returns:
            Zero decisions (held), one, or several (a release plus the new
            event).
        """
        event_type, event_time, metadata = event

        if not self.enabled:
            # Pass-through: today's behavior exactly, no holds.
            return [FilterDecision(event_type, event_time, metadata, notify=True, record=True)]

        if event_type == FalconEvent.DEPARTED:
            return self._process_departed(event_time, metadata, now)
        if event_type == FalconEvent.ARRIVED:
            return self._process_arrived(event_time, metadata, now)

        # ROOSTING (and anything else) passes through untouched — it has no
        # event-store row today.
        return [FilterDecision(event_type, event_time, metadata, notify=True, record=False)]

    def tick(self, now: datetime) -> list[FilterDecision]:
        """Advance time: release expired holds, emit due damping summaries.

        Called once per poll by the integration.
        """
        if not self.enabled:
            return []

        decisions: list[FilterDecision] = []

        if self._held is not None and self._window_expired(now):
            decisions.append(self._release(now))

        self._prune(now)
        self._update_damped(now)

        if self._damped and self._summary_due(now):
            decisions.append(self._make_summary(now))

        return decisions

    def flush(self, now: datetime) -> list[FilterDecision]:
        """Release any held departure immediately (shutdown path).

        No row is ever lost on SIGTERM: the held DEPARTED is released
        regardless of its merge window.
        """
        if self._held is not None:
            return [self._release(now)]
        return []

    def state_info(self) -> dict[str, Any]:
        """Diagnostic snapshot of filter state."""
        held_time = self._held[0] if self._held is not None else None
        return {
            "enabled": self.enabled,
            "held_departure": held_time.isoformat() if held_time else None,
            "chain_segments": self._chain_segments,
            "chain_visit_start": (
                self._chain_visit_start.isoformat() if self._chain_visit_start else None
            ),
            "damped": self._damped,
            "arrivals_in_window": len(self._arrivals),
            "merge_window_seconds": self.merge_window_seconds,
            "min_significant_seconds": self.min_significant_seconds,
            "damping_arrivals_threshold": self.damping_arrivals_threshold,
            "damping_window_hours": self.damping_window_hours,
        }

    # ── event handling ────────────────────────────────────────────────────

    def _process_departed(
        self, event_time: datetime, metadata: EventMetadata, now: datetime
    ) -> list[FilterDecision]:
        decisions: list[FilterDecision] = []

        if self._held is not None:
            # Defensive: the state machine cannot fire DEPARTED twice without
            # an intervening ARRIVED. Release the stale hold rather than
            # silently overwrite it.
            logger.warning("DEPARTED while another is held — releasing the stale hold")
            decisions.append(self._release(now))

        merged = dict(metadata)
        if self._chain_visit_start is not None:
            # Continuation chain in progress: the merged row spans the first
            # segment's visit_start to this departure's visit_end.
            merged["visit_start"] = self._chain_visit_start
            visit_end = merged.get("visit_end")
            if isinstance(visit_end, datetime):
                duration = (visit_end - self._chain_visit_start).total_seconds()
                merged["visit_duration_seconds"] = duration
                if "total_visit_duration" in merged:
                    merged["total_visit_duration"] = duration

        self._held = (event_time, merged)

        if self.merge_window_seconds <= 0 or self._window_expired(now):
            # Merging disabled, or the window (measured from event_time,
            # which already trails ``now`` by exit_timeout) has no time
            # left — release immediately.
            decisions.append(self._release(now))

        return decisions

    def _process_arrived(
        self, event_time: datetime, metadata: EventMetadata, now: datetime
    ) -> list[FilterDecision]:
        decisions: list[FilterDecision] = []

        if self._held is not None:
            held_time, held_metadata = self._held
            in_window = (
                self.merge_window_seconds > 0
                and (now - held_time).total_seconds() <= self.merge_window_seconds
            )
            if in_window:
                # Continuation of the same visit: swallow both events. The
                # held row stays pending; the chain remembers the first
                # segment's visit_start; the continuation's arrival clip is
                # marked for discard on a zero-surface decision.
                chain_start = held_metadata.get("visit_start")
                if isinstance(chain_start, datetime):
                    self._chain_visit_start = chain_start
                self._chain_segments += 1
                self._held = None
                logger.event(
                    f"🔗 Re-arrival within merge window — visit continues "
                    f"(segment {self._chain_segments})"
                )
                decisions.append(
                    FilterDecision(
                        FalconEvent.ARRIVED,
                        event_time,
                        metadata,
                        notify=False,
                        record=False,
                        merged_segments=self._chain_segments,
                        discard_arrival_clip=True,
                    )
                )
                return decisions

            # Window expired: the held departure was real. Release it, then
            # treat this arrival as a fresh pass-through.
            decisions.append(self._release(now))

        self._note_arrival(now)
        decisions.append(
            FilterDecision(
                FalconEvent.ARRIVED,
                event_time,
                metadata,
                notify=not self._damped,
                record=False,
            )
        )
        return decisions

    # ── release / significance ────────────────────────────────────────────

    def _release(self, now: datetime) -> FilterDecision:
        """Release the held DEPARTED as a surfaced (or de-surfaced) row."""
        assert self._held is not None, "release called with nothing held"
        event_time, metadata = self._held
        segments = self._chain_segments

        duration = self._detection_duration(metadata)
        insignificant = self.min_significant_seconds > 0 and duration < self.min_significant_seconds
        notify = not insignificant and not self._damped

        self._recent_durations.append((now, duration))
        self._held = None
        self._chain_visit_start = None
        self._chain_segments = 1

        if insignificant:
            logger.event(
                f"🔎 Visit below significance threshold ({duration:.0f}s < "
                f"{self.min_significant_seconds:.0f}s) — recorded log-only"
            )

        return FilterDecision(
            FalconEvent.DEPARTED,
            event_time,
            metadata,
            notify=notify,
            record=True,
            merged_segments=segments,
            insignificant=insignificant,
        )

    @staticmethod
    def _detection_duration(metadata: EventMetadata) -> float:
        """Detection-duration from (merged) metadata.

        ``visit_end − visit_start`` — both detection timestamps from the
        state machine, so the exit_timeout tail is already excluded.
        """
        visit_start = metadata.get("visit_start")
        visit_end = metadata.get("visit_end")
        if isinstance(visit_start, datetime) and isinstance(visit_end, datetime):
            return (visit_end - visit_start).total_seconds()
        return float(metadata.get("visit_duration_seconds") or 0.0)

    def _window_expired(self, now: datetime) -> bool:
        assert self._held is not None
        held_time = self._held[0]
        return (now - held_time).total_seconds() > self.merge_window_seconds

    # ── damping ───────────────────────────────────────────────────────────

    def _note_arrival(self, now: datetime) -> None:
        self._arrivals.append(now)
        self._prune(now)
        self._update_damped(now)

    def _prune(self, now: datetime) -> None:
        window_seconds = self.damping_window_hours * 3600
        while self._arrivals and (now - self._arrivals[0]).total_seconds() > window_seconds:
            self._arrivals.popleft()
        while (
            self._recent_durations
            and (now - self._recent_durations[0][0]).total_seconds() > window_seconds
        ):
            self._recent_durations.popleft()

    def _update_damped(self, now: datetime) -> None:
        active = (
            self.damping_arrivals_threshold > 0
            and len(self._arrivals) > self.damping_arrivals_threshold
        )
        if active and not self._damped:
            self._damped = True
            self._last_summary_time = None  # first tick emits a summary
            logger.event(
                f"🔕 Activity damping ON: {len(self._arrivals)} arrivals in the last "
                f"{self.damping_window_hours}h (threshold {self.damping_arrivals_threshold}) "
                f"— switching to summary notifications"
            )
        elif not active and self._damped:
            self._damped = False
            logger.event("🔔 Activity damping OFF — normal notifications resume")

    def _summary_due(self, now: datetime) -> bool:
        if self._last_summary_time is None:
            return True
        window_seconds = self.damping_window_hours * 3600
        return (now - self._last_summary_time).total_seconds() >= window_seconds

    def _make_summary(self, now: datetime) -> FilterDecision:
        durations = [duration for _, duration in self._recent_durations]
        median_duration = statistics.median(durations) if durations else 0.0
        self._last_summary_time = now
        return FilterDecision(
            None,
            now,
            {
                "count": len(self._arrivals),
                "median_duration_seconds": median_duration,
                "window_hours": self.damping_window_hours,
            },
            notify=True,
            record=False,
            is_summary=True,
        )
