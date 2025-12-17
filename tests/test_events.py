"""Tests for kanyo.detection.events module."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestFalconEvent:
    """Tests for FalconEvent dataclass."""

    def test_creation(self):
        """FalconEvent can be created with required fields."""
        from kanyo.detection.events import FalconEvent

        event = FalconEvent(
            event_type="falcon_enter",
            timestamp=datetime.now(),
            confidence=0.85,
            frame_number=1234,
        )
        assert event.event_type == "falcon_enter"
        assert event.confidence == 0.85
        assert event.frame_number == 1234

    def test_to_dict(self):
        """FalconEvent serializes to dict with ISO timestamp."""
        from kanyo.detection.events import FalconEvent

        ts = datetime(2025, 12, 17, 10, 30, 0)
        event = FalconEvent(
            event_type="falcon_exit",
            timestamp=ts,
            confidence=0.92,
        )
        d = event.to_dict()

        assert d["event_type"] == "falcon_exit"
        assert d["timestamp"] == "2025-12-17T10:30:00"
        assert d["confidence"] == 0.92

    def test_event_types(self):
        """Valid event types are accepted."""
        from kanyo.detection.events import FalconEvent

        for event_type in ["falcon_enter", "falcon_exit", "falcon_visit"]:
            event = FalconEvent(event_type=event_type, timestamp=datetime.now())
            assert event.event_type == event_type


class TestFalconVisit:
    """Tests for FalconVisit dataclass."""

    def test_creation(self):
        """FalconVisit can be created."""
        from kanyo.detection.events import FalconVisit

        start = datetime.now()
        visit = FalconVisit(start_time=start, peak_confidence=0.9)

        assert visit.start_time == start
        assert visit.end_time is None
        assert visit.peak_confidence == 0.9
        assert visit.is_active

    def test_duration_calculation(self):
        """Duration is calculated correctly."""
        from kanyo.detection.events import FalconVisit

        start = datetime.now() - timedelta(minutes=5, seconds=30)
        end = datetime.now()
        visit = FalconVisit(start_time=start, end_time=end, peak_confidence=0.8)

        assert visit.duration_seconds == pytest.approx(330, abs=1)
        assert "5m" in visit.duration_str

    def test_active_state(self):
        """is_active reflects whether visit is ongoing."""
        from kanyo.detection.events import FalconVisit

        # Active visit (no end time)
        active = FalconVisit(start_time=datetime.now())
        assert active.is_active

        # Completed visit
        completed = FalconVisit(
            start_time=datetime.now() - timedelta(minutes=1),
            end_time=datetime.now(),
        )
        assert not completed.is_active

    def test_to_dict(self):
        """FalconVisit serializes to dict."""
        from kanyo.detection.events import FalconVisit

        start = datetime(2025, 12, 17, 10, 0, 0)
        end = datetime(2025, 12, 17, 10, 5, 0)
        visit = FalconVisit(
            start_time=start,
            end_time=end,
            peak_confidence=0.95,
            thumbnail_path="data/thumbs/falcon.jpg",
        )
        d = visit.to_dict()

        assert d["start_time"] == "2025-12-17T10:00:00"
        assert d["end_time"] == "2025-12-17T10:05:00"
        assert d["duration_seconds"] == 300
        assert d["peak_confidence"] == 0.95
        assert d["thumbnail_path"] == "data/thumbs/falcon.jpg"


class TestEventStore:
    """Tests for EventStore persistence."""

    def test_creation(self):
        """EventStore creates parent directories."""
        from kanyo.detection.events import EventStore

        with TemporaryDirectory() as tmpdir:
            events_file = Path(tmpdir) / "subdir" / "events.json"
            store = EventStore(events_path=events_file)

            assert store.events_path == events_file
            assert events_file.parent.exists()

    def test_load_empty(self):
        """Loading non-existent file returns empty list."""
        from kanyo.detection.events import EventStore

        with TemporaryDirectory() as tmpdir:
            store = EventStore(events_path=Path(tmpdir) / "events.json")
            events = store.load()

            assert events == []

    def test_append_and_load(self):
        """Events can be appended and reloaded."""
        from kanyo.detection.events import EventStore, FalconVisit

        with TemporaryDirectory() as tmpdir:
            events_file = Path(tmpdir) / "events.json"
            store = EventStore(events_path=events_file)

            visit = FalconVisit(
                start_time=datetime.now() - timedelta(minutes=5),
                end_time=datetime.now(),
                peak_confidence=0.88,
            )
            store.append(visit)

            # Reload from file
            loaded = store.load()
            assert len(loaded) == 1
            assert loaded[0]["peak_confidence"] == 0.88

    def test_multiple_appends(self):
        """Multiple events accumulate."""
        from kanyo.detection.events import EventStore, FalconVisit

        with TemporaryDirectory() as tmpdir:
            store = EventStore(events_path=Path(tmpdir) / "events.json")

            for i in range(3):
                visit = FalconVisit(
                    start_time=datetime.now(),
                    end_time=datetime.now(),
                    peak_confidence=0.5 + i * 0.1,
                )
                store.append(visit)

            loaded = store.load()
            assert len(loaded) == 3

    def test_get_today_visits(self):
        """get_today_visits filters by date."""
        from kanyo.detection.events import EventStore, FalconVisit

        with TemporaryDirectory() as tmpdir:
            store = EventStore(events_path=Path(tmpdir) / "events.json")

            # Today's visit
            today = FalconVisit(
                start_time=datetime.now(),
                end_time=datetime.now(),
            )
            store.append(today)

            today_visits = store.get_today_visits()
            assert len(today_visits) == 1

    def test_json_file_format(self):
        """Events are stored as valid JSON."""
        from kanyo.detection.events import EventStore, FalconVisit

        with TemporaryDirectory() as tmpdir:
            events_file = Path(tmpdir) / "events.json"
            store = EventStore(events_path=events_file)

            visit = FalconVisit(start_time=datetime.now(), end_time=datetime.now())
            store.append(visit)

            # Verify file is valid JSON
            with open(events_file) as f:
                data = json.load(f)
            assert isinstance(data, list)
            assert len(data) == 1
