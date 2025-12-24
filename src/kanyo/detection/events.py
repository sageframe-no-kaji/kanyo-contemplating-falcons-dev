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
class FalconEvent:
    """Base event with timestamp."""

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
    """

    start_time: datetime
    end_time: datetime | None = None
    peak_confidence: float = 0.0
    thumbnail_path: str | None = None
    arrival_clip_path: str | None = None
    departure_clip_path: str | None = None
    id: str = field(default="")

    def __post_init__(self):
        \"\"\"Generate ID from start_time if not provided.\"\"\"
        if not self.id:
            self.id = self.start_time.strftime("%Y%m%d_%H%M%S")

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
        }


class EventStore:
    """
    Persists falcon events to JSON file.

    Thread-safe append-only storage with automatic file creation.
    """

    def __init__(self, events_path: str | Path = "data/events.json"):
        self.events_path = Path(events_path)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[dict]:
        """Load all events from file."""
        if not self.events_path.exists():
            return []
        try:
            with open(self.events_path) as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning("Corrupted events file, starting fresh")
            return []

    def save(self, events: list[dict]) -> None:
        """Save events to file."""
        with open(self.events_path, "w") as f:
            json.dump(events, f, indent=2)

    def append(self, event: FalconEvent | FalconVisit) -> None:
        """Append a single event."""
        events = self.load()
        events.append(event.to_dict())
        self.save(events)
        logger.debug(f"Saved event: {event}")

    def get_visits(self) -> list[dict]:
        """Get all falcon_visit events."""
        return [
            e for e in self.load() if e.get("event_type") == "falcon_visit" or "start_time" in e
        ]

    def get_today_visits(self) -> list[dict]:
        """Get visits from today."""
        today = datetime.now().date().isoformat()
        return [v for v in self.get_visits() if v.get("start_time", "").startswith(today)]
