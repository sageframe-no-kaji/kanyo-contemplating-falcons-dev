"""Tests for the FalconVisit row enhancements (viewer contract follow-ups).

Covers the provisional-row protocol (row written at confirmation with
end_time null, replaced in place at close, crash leftovers tolerated),
microsecond ids, and visit_clip_paths accumulation across merged segments.
All changes are additive on the viewer contract: unknown fields are ignored
and a null end_time is the viewer's existing "ongoing visit" state.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.detection.event_types import FalconEvent
from kanyo.detection.events import EventStore, FalconVisit

T0 = datetime(2026, 7, 16, 10, 0, 0)


def ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_monitor(clips_dir: str, real_store: bool = False, **kwargs):
    """BufferMonitor with mocked externals (optionally a real EventStore)."""
    patches = [
        patch("kanyo.detection.buffer_monitor.StreamCapture"),
        patch("kanyo.detection.buffer_monitor.FalconDetector"),
        patch("kanyo.detection.buffer_monitor.FrameBuffer"),
        patch("kanyo.detection.buffer_monitor.VisitRecorder"),
        patch("kanyo.detection.buffer_monitor.BufferClipManager"),
        patch("kanyo.detection.buffer_monitor.FalconEventHandler"),
        patch("kanyo.detection.buffer_monitor.FalconStateMachine"),
        patch("kanyo.detection.buffer_monitor.ArrivalClipRecorder"),
    ]
    if not real_store:
        patches.append(patch("kanyo.detection.buffer_monitor.EventStore"))
    started = [p.start() for p in patches]
    try:
        monitor = BufferMonitor(
            stream_url="test",
            clips_dir=clips_dir,
            presence_enabled=False,
            full_config={},
            **kwargs,
        )
    finally:
        for p in patches:
            p.stop()
    del started
    return monitor


def confirm_arrival(monitor, arrival_time: datetime) -> None:
    monitor.arrival_pending = True
    monitor.arrival_pending_start = arrival_time
    monitor.arrival_detection_count = 5
    monitor.arrival_frame_count = 10
    monitor.arrival_clip_recorder.get_temp_path.return_value = None
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor._frame_now = arrival_time
    monitor._confirm_arrival()


def drive_departure(monitor, visit_start: datetime, visit_end: datetime) -> None:
    monitor.arrival_pending = False
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor.visit_recorder.stop_recording.return_value = (
        f"/fake/visit_{visit_start.strftime('%H%M%S')}.mp4",
        {"visit_start": visit_start, "duration_seconds": 0},
    )
    monitor.clip_manager.create_departure_clip.return_value = True
    metadata = {
        "visit_start": visit_start,
        "visit_end": visit_end,
        "visit_duration_seconds": (visit_end - visit_start).total_seconds(),
    }
    monitor._frame_now = visit_end
    monitor._handle_event(FalconEvent.DEPARTED, visit_end, metadata)


class TestMicrosecondIds:
    def test_id_includes_microseconds(self):
        visit = FalconVisit(start_time=datetime(2026, 7, 16, 10, 0, 0, 123456))
        assert visit.id == "20260716_100000_123456"

    def test_same_second_visits_do_not_collide(self):
        a = FalconVisit(start_time=datetime(2026, 7, 16, 10, 0, 0, 100))
        b = FalconVisit(start_time=datetime(2026, 7, 16, 10, 0, 0, 200))
        assert a.id != b.id

    def test_explicit_id_untouched(self):
        visit = FalconVisit(start_time=T0, id="custom")
        assert visit.id == "custom"


class TestVisitClipPathsField:
    def test_defaults_empty_and_serialized(self):
        visit = FalconVisit(start_time=T0, end_time=ts(60))
        assert visit.visit_clip_paths == []
        assert visit.to_dict()["visit_clip_paths"] == []

    def test_serializes_paths(self):
        visit = FalconVisit(
            start_time=T0,
            end_time=ts(60),
            visit_clip_paths=["clips/2026-07-16/a.mp4", "clips/2026-07-16/b.mp4"],
        )
        d = visit.to_dict()
        assert d["visit_clip_paths"] == ["clips/2026-07-16/a.mp4", "clips/2026-07-16/b.mp4"]


class TestEventStoreUpsert:
    def _store(self, tmp_path) -> EventStore:
        return EventStore(clips_dir=Path(tmp_path) / "clips", timezone_config={})

    def _rows(self, store: EventStore, visit: FalconVisit) -> list[dict]:
        return store.load(store._get_events_path(visit))

    def test_upsert_appends_when_absent(self, tmp_path):
        store = self._store(tmp_path)
        visit = FalconVisit(start_time=T0, end_time=ts(60))
        store.upsert(visit)
        rows = self._rows(store, visit)
        assert len(rows) == 1
        assert rows[0]["id"] == visit.id

    def test_finalize_replaces_provisional_in_place(self, tmp_path):
        store = self._store(tmp_path)
        provisional = FalconVisit(start_time=T0)  # end_time null
        store.upsert(provisional)
        assert self._rows(store, provisional)[0]["end_time"] is None

        final = FalconVisit(start_time=T0, end_time=ts(300), peak_confidence=0.8)
        store.upsert(final)

        rows = self._rows(store, final)
        assert len(rows) == 1  # replaced, not appended
        assert rows[0]["end_time"] == ts(300).isoformat()
        assert rows[0]["peak_confidence"] == 0.8

    def test_crash_leftover_provisional_tolerated(self, tmp_path):
        """A stale provisional from a crashed run (different id) stays put;
        the new visit's rows are unaffected."""
        store = self._store(tmp_path)
        stale = FalconVisit(start_time=ts(-3600))  # crashed run, end_time null
        store.upsert(stale)

        final = FalconVisit(start_time=T0, end_time=ts(300))
        store.upsert(final)  # no provisional with this id on file — appends

        rows = self._rows(store, final)
        assert len(rows) == 2
        assert rows[0]["id"] == stale.id
        assert rows[0]["end_time"] is None  # still "ongoing" — tolerated
        assert rows[1]["id"] == final.id

    def test_discard_removes_row(self, tmp_path):
        store = self._store(tmp_path)
        visit = FalconVisit(start_time=T0)
        store.upsert(visit)
        store.discard(visit)
        assert self._rows(store, visit) == []

    def test_discard_missing_row_is_noop(self, tmp_path):
        store = self._store(tmp_path)
        other = FalconVisit(start_time=ts(500), end_time=ts(600))
        store.upsert(other)
        store.discard(FalconVisit(start_time=T0))
        assert len(self._rows(store, other)) == 1


class TestProvisionalRowWiring:
    def test_confirm_arrival_writes_provisional(self, tmp_path):
        monitor = make_monitor(str(tmp_path))
        confirm_arrival(monitor, T0)

        monitor.event_store.upsert.assert_called_once()
        row = monitor.event_store.upsert.call_args[0][0]
        assert row.end_time is None
        assert row.id == T0.strftime("%Y%m%d_%H%M%S_%f")
        assert monitor._provisional_visit is row

    def test_confirm_startup_writes_provisional(self, tmp_path):
        monitor = make_monitor(str(tmp_path))
        monitor.startup_pending = True
        monitor.startup_pending_start = T0
        monitor.startup_detection_count = 5
        monitor.startup_frame_count = 10
        monitor.notify_on_startup = False

        monitor._confirm_startup_presence(ts(10))

        monitor.event_store.upsert.assert_called_once()
        row = monitor.event_store.upsert.call_args[0][0]
        assert row.end_time is None
        assert row.start_time == T0

    def test_continuation_confirm_writes_no_second_provisional(self, tmp_path):
        """A re-arrival swallowed by the merge window is a continuation: its
        row of record is the held first segment's row."""
        monitor = make_monitor(
            str(tmp_path),
            significance_filter_enabled=True,
            merge_window_seconds=300,
        )
        confirm_arrival(monitor, T0)  # provisional 1
        drive_departure(monitor, T0, ts(600))  # held
        confirm_arrival(monitor, ts(700))  # continuation — swallowed

        provisionals = [
            c.args[0]
            for c in monitor.event_store.upsert.call_args_list
            if c.args[0].end_time is None
        ]
        assert len(provisionals) == 1
        assert provisionals[0].start_time == T0

    def test_departure_finalizes_with_same_id(self, tmp_path):
        monitor = make_monitor(str(tmp_path))
        confirm_arrival(monitor, T0)
        provisional = monitor.event_store.upsert.call_args[0][0]

        drive_departure(monitor, T0, ts(300))

        final = monitor.event_store.upsert.call_args[0][0]
        assert final.end_time == ts(300)
        assert final.id == provisional.id  # replaces the provisional row
        assert monitor._provisional_visit is None

    def test_departure_without_row_discards_provisional(self, tmp_path):
        """Recorder yields no file/metadata: the provisional must not sit as
        a forever-ongoing visit."""
        monitor = make_monitor(str(tmp_path))
        confirm_arrival(monitor, T0)
        provisional = monitor._provisional_visit
        assert provisional is not None

        monitor.arrival_pending = False
        monitor.arrival_clip_recorder.is_recording.return_value = False
        monitor.visit_recorder.stop_recording.return_value = (None, None)
        monitor._frame_now = ts(300)
        monitor._handle_event(FalconEvent.DEPARTED, ts(300), {})

        monitor.event_store.discard.assert_called_once_with(provisional)
        assert monitor._provisional_visit is None

    def test_abandoned_visit_discards_provisional(self, tmp_path):
        """Outage-exceeded reset (no departure event) removes the row."""
        monitor = make_monitor(str(tmp_path))
        confirm_arrival(monitor, T0)
        provisional = monitor._provisional_visit

        monitor._reset_pending_states()

        monitor.event_store.discard.assert_called_once_with(provisional)
        assert monitor._provisional_visit is None

    def test_reset_keeps_provisional_when_row_held_for_release(self, tmp_path):
        """With the departure row already held by the filter, the provisional
        stays on file — the release will replace it by id."""
        monitor = make_monitor(
            str(tmp_path),
            significance_filter_enabled=True,
            merge_window_seconds=300,
        )
        confirm_arrival(monitor, T0)
        drive_departure(monitor, T0, ts(600))  # held; pending row exists
        assert monitor._pending_visit_row is not None

        monitor._reset_pending_states()

        monitor.event_store.discard.assert_not_called()
        assert monitor._provisional_visit is None

    def test_full_lifecycle_with_real_store(self, tmp_path):
        """End-to-end JSON consistency: provisional written at confirmation,
        replaced by the finalized row at close — one row, closed."""
        monitor = make_monitor(str(tmp_path), real_store=True)
        assert isinstance(monitor.event_store, EventStore)

        confirm_arrival(monitor, T0)
        probe = FalconVisit(start_time=T0)
        events_path = monitor.event_store._get_events_path(probe)
        rows = monitor.event_store.load(events_path)
        assert len(rows) == 1
        assert rows[0]["end_time"] is None  # the viewer's "ongoing visit"

        drive_departure(monitor, T0, ts(300))
        rows = monitor.event_store.load(events_path)
        assert len(rows) == 1
        assert rows[0]["end_time"] == ts(300).isoformat()
        assert rows[0]["visit_clip_paths"] == ["/fake/visit_100000.mp4"]


class TestVisitClipPathsWiring:
    def test_normal_departure_row_carries_visit_file(self, tmp_path):
        monitor = make_monitor(str(tmp_path))
        drive_departure(monitor, T0, ts(300))
        row = monitor.event_store.upsert.call_args[0][0]
        assert row.visit_clip_paths == ["/fake/visit_100000.mp4"]

    def test_roosting_stop_row_carries_roost_file(self, tmp_path):
        monitor = make_monitor(str(tmp_path))
        monitor.roosting_mode_active = True
        monitor.roosting_recording_mode = "stop"
        monitor._roosting_visit_metadata = {
            "visit_start": T0,
            "visit_file": "clips/2026-07-16/roost.mp4",
        }
        monitor.clip_manager.create_clip_from_buffer.return_value = True
        monitor.clip_manager.clip_departure_before = 30
        monitor.arrival_pending = False
        monitor.arrival_clip_recorder.is_recording.return_value = False

        metadata = {"visit_start": T0, "visit_end": ts(3600)}
        monitor._frame_now = ts(3600)
        monitor._handle_event(FalconEvent.DEPARTED, ts(3600), metadata)

        row = monitor.event_store.upsert.call_args[0][0]
        assert row.visit_clip_paths == ["clips/2026-07-16/roost.mp4"]

    def test_merged_row_accumulates_segment_files(self, tmp_path):
        monitor = make_monitor(
            str(tmp_path),
            significance_filter_enabled=True,
            merge_window_seconds=300,
        )
        drive_departure(monitor, T0, ts(600))  # held
        confirm_arrival(monitor, ts(700))  # continuation
        drive_departure(monitor, ts(700), ts(900))  # still held

        # Window expires — one merged row with both segment files, in order.
        monitor._frame_now = ts(900 + 301)
        monitor._execute_decisions(monitor.significance_filter.tick(ts(900 + 301)))

        finalized = [
            c.args[0]
            for c in monitor.event_store.upsert.call_args_list
            if c.args[0].end_time is not None
        ]
        assert len(finalized) == 1
        assert finalized[0].visit_clip_paths == [
            "/fake/visit_100000.mp4",
            "/fake/visit_101140.mp4",
        ]
        assert finalized[0].merged_segments == 2
