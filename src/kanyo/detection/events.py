"""
Event models for falcon detection tracking.

Provides dataclasses for tracking falcon visits and persisting to JSON.
All events include ISO timestamps and duration calculations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)

EventType = Literal["falcon_enter", "falcon_exit", "falcon_visit"]


@dataclass
class EventRecord:
    """Base event record for persistence."""

    event_type: EventType
    timestamp: datetime
    confidence: float = 0.0
    frame_number: int = 0
    thumbnail_path: str | None = None

    def to_dict(self) -> dict:
        """Serialize for JSON."""
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "confidence": round(self.confidence, 3),
            "frame_number": self.frame_number,
            "thumbnail_path": self.thumbnail_path,
        }


@dataclass
class FalconVisit:
    """
    A complete falcon visit (enter → exit).

    Tracks start/end times, duration, and associated thumbnails/clips.

    Path field semantics (viewer contract): ``thumbnail_path``,
    ``arrival_clip_path``, ``departure_clip_path``, and every entry of
    ``visit_clip_paths`` are detector-side paths (``clips/<date>/<file>`` as
    the detector wrote them). Only the BASENAME is authoritative for
    consumers — the directory prefix reflects the detector's local layout
    and may not exist on the consumer's filesystem; viewers should resolve
    basenames against their own clips mount.

    Row lifecycle: a provisional row (``end_time`` null — the viewer's
    "ongoing visit" state) is written when the visit is confirmed and
    replaced in place (matched by ``id``) when the visit closes. A crash can
    leave a provisional row behind; consumers must tolerate rows whose
    ``end_time`` stays null.
    """

    start_time: datetime
    end_time: datetime | None = None
    peak_confidence: float = 0.0
    thumbnail_path: str | None = None
    arrival_clip_path: str | None = None
    departure_clip_path: str | None = None
    # Significance filter surface flags (ho-09 / 025-B). Additive with safe
    # defaults so existing rows and to_dict() consumers are unaffected.
    insignificant: bool = False  # below min_significant_seconds: recorded log-only
    merged_segments: int = 1  # >= 2 when this row spans merged visit segments
    # Bird count tracking (issue #3). None when count tracking is disabled;
    # otherwise the highest confirmed concurrent bird count during the visit.
    # Additive: consumers that don't know the field ignore it.
    max_concurrent_birds: int | None = None
    # Visit segment recordings (viewer contract review follow-up). One entry
    # per visit file; a merged visit accumulates one entry per segment in
    # chronological order. Same basename-authoritative semantics as the
    # other path fields.
    visit_clip_paths: list[str] = field(default_factory=list)
    id: str = field(default="")

    def __post_init__(self):
        """Generate ID from start_time if not provided.

        Microseconds are included (``%f``) so two visits starting within the
        same wall-clock second — merge-window edge cases, rapid swap storms —
        can never collide on id. The id doubles as the replace-by-id key for
        provisional rows, so collisions would corrupt the store.
        """
        if not self.id:
            self.id = self.start_time.strftime("%Y%m%d_%H%M%S_%f")

    @property
    def duration(self) -> timedelta | None:
        """Duration of visit."""
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    @property
    def duration_seconds(self) -> int:
        """Duration in seconds."""
        d = self.duration
        return int(d.total_seconds()) if d else 0

    @property
    def duration_str(self) -> str:
        """Human-readable duration."""
        d = self.duration
        if d is None:
            return "ongoing"
        minutes, seconds = divmod(int(d.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"

    @property
    def is_active(self) -> bool:
        """True if visit is still ongoing."""
        return self.end_time is None

    def to_dict(self) -> dict:
        """Serialize for JSON."""
        return {
            "id": self.id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "duration_str": self.duration_str,
            "peak_confidence": round(self.peak_confidence, 3),
            "thumbnail_path": self.thumbnail_path,
            "arrival_clip_path": self.arrival_clip_path,
            "departure_clip_path": self.departure_clip_path,
            "insignificant": self.insignificant,
            "merged_segments": self.merged_segments,
            "max_concurrent_birds": self.max_concurrent_birds,
            "visit_clip_paths": self.visit_clip_paths,
        }


class EventStore:
    """
    Persists falcon events to JSON file.

    Writes events to date-specific files based on event timestamp in stream's local timezone.
    """

    def __init__(
        self,
        clips_dir: str | Path = "clips",
        timezone_config: dict | None = None,
    ):
        """
        Initialize event store.

        Args:
            clips_dir: Base clips directory (e.g., "clips")
            timezone_config: Full config dict with timezone info for proper date calculation
        """
        self.clips_dir = Path(clips_dir)
        self.timezone_config = timezone_config or {}

    def _get_event_date(self, event: EventRecord | FalconVisit) -> str:
        """
        Get the date string for an event based on its timestamp in stream local timezone.

        Args:
            event: Event with start_time or timestamp

        Returns:
            Date string in YYYY-MM-DD format
        """
        from kanyo.utils.config import get_now_tz

        # Get event timestamp
        if isinstance(event, FalconVisit):
            event_time = event.start_time
        else:
            event_time = event.timestamp

        # If event_time is already timezone-aware and in stream timezone, use it directly
        # Otherwise, convert using get_now_tz approach
        if event_time.tzinfo is not None:
            # Already timezone-aware, use its date
            date_str = event_time.strftime("%Y-%m-%d")
        else:
            # Naive datetime - shouldn't happen, but handle it
            now_tz = get_now_tz(self.timezone_config)
            date_str = now_tz.strftime("%Y-%m-%d")

        return date_str

    def _get_events_path(self, event: EventRecord | FalconVisit) -> Path:
        """Get the events file path for a given event."""
        date_str = self._get_event_date(event)
        date_dir = self.clips_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)
        return date_dir / f"events_{date_str}.json"

    def load(self, events_path: Path | None = None) -> list[dict]:
        """Load all events from file."""
        if events_path is None:
            # For backward compatibility - use today's file
            from kanyo.utils.config import get_now_tz

            date_str = get_now_tz(self.timezone_config).strftime("%Y-%m-%d")
            events_path = self.clips_dir / date_str / f"events_{date_str}.json"

        if not events_path.exists():
            return []
        try:
            with open(events_path) as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning("Corrupted events file, starting fresh")
            return []

    def save(self, events: list[dict], events_path: Path) -> None:
        """Save events to file."""
        with open(events_path, "w") as f:
            json.dump(events, f, indent=2)

    def append(self, event: EventRecord | FalconVisit) -> None:
        """Append a single event to the appropriate date file."""
        events_path = self._get_events_path(event)
        events = self.load(events_path)
        events.append(event.to_dict())
        self.save(events, events_path)
        logger.debug(f"Saved event to {events_path.name}: {event}")

    def upsert(self, visit: FalconVisit) -> None:
        """Write a visit row, replacing any existing row with the same id.

        The provisional-row protocol: a row with ``end_time`` null is written
        at visit confirmation and REPLACED in place by the finalized row at
        visit close (both derive the same id from the same ``start_time``, so
        the date file is also the same by construction). With no matching id
        on file — a crash between runs, a hand-edited file — the row is
        appended instead: finalization never fails, and stale provisional
        rows from crashed runs are tolerated rather than cleaned (the viewer
        reads a null ``end_time`` as an ongoing visit).
        """
        events_path = self._get_events_path(visit)
        events = self.load(events_path)
        for i, row in enumerate(events):
            if row.get("id") == visit.id:
                events[i] = visit.to_dict()
                self.save(events, events_path)
                logger.debug(f"Replaced visit row {visit.id} in {events_path.name}")
                return
        events.append(visit.to_dict())
        self.save(events, events_path)
        logger.debug(f"Saved visit row {visit.id} to {events_path.name}")

    def discard(self, visit: FalconVisit) -> None:
        """Remove a visit row by id (abandoned provisional rows).

        Used when a confirmed visit is abandoned without a departure event
        (stream outage exceeded) — the provisional row would otherwise sit
        as a forever-ongoing visit. Missing rows are a no-op.
        """
        events_path = self._get_events_path(visit)
        events = self.load(events_path)
        remaining = [row for row in events if row.get("id") != visit.id]
        if len(remaining) != len(events):
            self.save(remaining, events_path)
            logger.debug(f"Discarded visit row {visit.id} from {events_path.name}")

    def get_visits(self, events_path: Path | None = None) -> list[dict]:
        """Get all falcon_visit events."""
        return [
            e
            for e in self.load(events_path)
            if e.get("event_type") == "falcon_visit" or "start_time" in e
        ]

    def get_today_visits(self) -> list[dict]:
        """Get visits from today (in stream's local timezone)."""
        from kanyo.utils.config import get_now_tz

        today = get_now_tz(self.timezone_config).date().isoformat()
        date_dir = self.clips_dir / today
        events_path = date_dir / f"events_{today}.json"
        return [
            v for v in self.get_visits(events_path) if v.get("start_time", "").startswith(today)
        ]
