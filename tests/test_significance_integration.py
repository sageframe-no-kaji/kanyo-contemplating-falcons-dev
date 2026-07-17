"""Integration tests for the significance filter in buffer_monitor (ho-09 / 025-B).

Recording mechanics run on raw events; notifications and event-store rows
flow through EventSignificanceFilter decisions. With the filter disabled the
routing is identical to the pre-filter behavior.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanyo.detection.buffer_monitor import BufferMonitor
from kanyo.detection.event_types import FalconEvent
from kanyo.detection.events import FalconVisit
from kanyo.utils.config import DEFAULTS, _validate
from kanyo.utils.output import get_output_path

T0 = datetime(2026, 7, 16, 10, 0, 0)


def ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_monitor(
    clips_dir: str = "clips",
    significance_filter_enabled: bool = True,
    merge_window_seconds: float = 300,
    min_significant_seconds: float = 30,
    damping_arrivals_threshold: int = 8,
    damping_window_hours: float = 1,
):
    """Return a BufferMonitor with all external dependencies mocked out."""
    with (
        patch("kanyo.detection.buffer_monitor.StreamCapture"),
        patch("kanyo.detection.buffer_monitor.FalconDetector"),
        patch("kanyo.detection.buffer_monitor.FrameBuffer"),
        patch("kanyo.detection.buffer_monitor.VisitRecorder"),
        patch("kanyo.detection.buffer_monitor.BufferClipManager"),
        patch("kanyo.detection.buffer_monitor.EventStore"),
        patch("kanyo.detection.buffer_monitor.FalconEventHandler"),
        patch("kanyo.detection.buffer_monitor.FalconStateMachine"),
        patch("kanyo.detection.buffer_monitor.ArrivalClipRecorder"),
    ):
        monitor = BufferMonitor(
            stream_url="test",
            clips_dir=clips_dir,
            presence_enabled=False,
            significance_filter_enabled=significance_filter_enabled,
            merge_window_seconds=merge_window_seconds,
            min_significant_seconds=min_significant_seconds,
            damping_arrivals_threshold=damping_arrivals_threshold,
            damping_window_hours=damping_window_hours,
            full_config={},
        )
    return monitor


def drive_departure(monitor, visit_start: datetime, visit_end: datetime) -> None:
    """Run a normal DEPARTED through _handle_event with mocked mechanics.

    The event time is visit_end (state-machine semantics) and the frame time
    is set to visit_end so the merge window runs its full length in tests.
    """
    monitor.arrival_pending = False
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor.visit_recorder.stop_recording.return_value = (
        "/fake/visit.mp4",
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


def confirm_arrival(monitor, arrival_time: datetime, now: datetime | None = None) -> None:
    """Run a confirmed arrival through _confirm_arrival."""
    monitor.arrival_pending = True
    monitor.arrival_pending_start = arrival_time
    monitor.arrival_detection_count = 5
    monitor.arrival_frame_count = 10
    monitor.arrival_clip_recorder.get_temp_path.return_value = None
    monitor.arrival_clip_recorder.is_recording.return_value = False
    monitor._frame_now = now or arrival_time
    monitor._confirm_arrival()


def tick(monitor, now: datetime) -> None:
    """Advance the filter as process_frame does once per poll."""
    monitor._frame_now = now
    monitor._execute_decisions(monitor.significance_filter.tick(now))


def departed_notifications(monitor) -> list:
    return [
        c
        for c in monitor.event_handler.handle_event.call_args_list
        if c.args[0] == FalconEvent.DEPARTED
    ]


def arrived_notifications(monitor) -> list:
    return [
        c
        for c in monitor.event_handler.handle_event.call_args_list
        if c.args[0] == FalconEvent.ARRIVED
    ]


class TestDisabledPassThrough:
    """significance_filter_enabled=false → routing identical to today."""

    def test_departure_surfaces_immediately(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path), significance_filter_enabled=False)

        drive_departure(monitor, ts(0), ts(20))  # even a 20s visit

        monitor.event_store.append.assert_called_once()
        row = monitor.event_store.append.call_args[0][0]
        assert isinstance(row, FalconVisit)
        assert row.insignificant is False
        assert row.merged_segments == 1
        assert len(departed_notifications(monitor)) == 1
        notification = departed_notifications(monitor)[0]
        assert notification.args[1] == ts(20)
        assert notification.args[2]["visit_start"] == ts(0)

    def test_confirmed_arrival_notifies_immediately(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path), significance_filter_enabled=False)

        confirm_arrival(monitor, ts(0))

        assert len(arrived_notifications(monitor)) == 1
        monitor.arrival_clip_recorder.rename_to_final.assert_called_once()
        monitor.visit_recorder.rename_to_final.assert_called_once()

    def test_rapid_pair_not_merged_when_disabled(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path), significance_filter_enabled=False)

        drive_departure(monitor, ts(0), ts(600))
        confirm_arrival(monitor, ts(720))
        drive_departure(monitor, ts(720), ts(900))

        assert monitor.event_store.append.call_count == 2
        assert len(departed_notifications(monitor)) == 2
        assert len(arrived_notifications(monitor)) == 1


class TestMergedVisit:
    def test_departed_then_rearrival_yields_one_merged_row(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path), merge_window_seconds=300)

        # First segment departs — held: no row, no notification.
        drive_departure(monitor, ts(0), ts(600))
        monitor.event_store.append.assert_not_called()
        assert departed_notifications(monitor) == []

        # Re-arrival inside the window — swallowed: no arrival notification,
        # arrival clip NOT finalized.
        confirm_arrival(monitor, ts(720))
        assert arrived_notifications(monitor) == []
        monitor.arrival_clip_recorder.rename_to_final.assert_not_called()
        # The visit recording still finalizes — files are the unit of storage.
        monitor.visit_recorder.rename_to_final.assert_called_once()

        # Second segment departs — still held.
        drive_departure(monitor, ts(720), ts(900))
        monitor.event_store.append.assert_not_called()

        # Window expires: ONE merged row + ONE departure notification.
        tick(monitor, ts(900 + 301))
        monitor.event_store.append.assert_called_once()
        row = monitor.event_store.append.call_args[0][0]
        assert row.start_time == ts(0)
        assert row.end_time == ts(900)
        assert row.merged_segments == 2
        assert row.insignificant is False
        # Departure clip comes from the last segment.
        expected_departure = str(get_output_path(str(tmp_path), ts(900), "departure", "mp4"))
        assert row.departure_clip_path == expected_departure

        notifications = departed_notifications(monitor)
        assert len(notifications) == 1
        assert notifications[0].args[2]["visit_start"] == ts(0)
        assert notifications[0].args[2]["visit_duration_seconds"] == 900.0

    def test_merged_row_takes_max_peak_confidence(self, tmp_path):
        monitor = make_monitor(clips_dir=str(tmp_path), merge_window_seconds=300)

        monitor._visit_peak_confidence = 0.9
        drive_departure(monitor, ts(0), ts(600))
        confirm_arrival(monitor, ts(700))
        monitor._visit_peak_confidence = 0.4
        drive_departure(monitor, ts(700), ts(900))

        tick(monitor, ts(900 + 301))
        row = monitor.event_store.append.call_args[0][0]
        assert row.peak_confidence == 0.9

    def test_continuation_arrival_clip_deleted(self, tmp_path):
        """The swallowed arrival's clip file (still .tmp at confirmation) is
        deleted instead of finalized."""
        monitor = make_monitor(clips_dir=str(tmp_path), merge_window_seconds=300)
        drive_departure(monitor, ts(0), ts(600))

        clip_tmp = Path(tmp_path) / "arrival_clip.mp4.tmp"
        clip_tmp.write_bytes(b"fake")
        monitor.arrival_pending = True
        monitor.arrival_pending_start = ts(720)
        monitor.arrival_detection_count = 5
        monitor.arrival_frame_count = 10
        monitor.arrival_clip_recorder.get_temp_path.return_value = clip_tmp
        monitor.arrival_clip_recorder.is_recording.return_value = True
        monitor._frame_now = ts(720)
        monitor._confirm_arrival()

        monitor.arrival_clip_recorder.stop_recording.assert_called_once()
        monitor.arrival_clip_recorder.rename_to_final.assert_not_called()
        assert not clip_tmp.exists()

    def test_lone_departure_released_at_expiry(self, tmp_path):
        """No re-arrival: row + notification surface at window expiry,
        content matching today's except timing."""
        monitor = make_monitor(clips_dir=str(tmp_path), merge_window_seconds=300)

        drive_departure(monitor, ts(0), ts(600))
        tick(monitor, ts(600 + 300))  # boundary — still held
        monitor.event_store.append.assert_not_called()

        tick(monitor, ts(600 + 301))
        monitor.event_store.append.assert_called_once()
        row = monitor.event_store.append.call_args[0][0]
        assert row.start_time == ts(0)
        assert row.end_time == ts(600)
        assert row.merged_segments == 1
        assert len(departed_notifications(monitor)) == 1


class TestInsignificantVisit:
    def test_short_visit_row_flagged_no_notification(self, tmp_path):
        monitor = make_monitor(
            clips_dir=str(tmp_path), merge_window_seconds=300, min_significant_seconds=30
        )

        drive_departure(monitor, ts(0), ts(20))
        tick(monitor, ts(20 + 301))

        monitor.event_store.append.assert_called_once()
        row = monitor.event_store.append.call_args[0][0]
        assert row.insignificant is True
        assert departed_notifications(monitor) == []


class TestDamping:
    def test_summary_replaces_individual_notifications(self, tmp_path):
        monitor = make_monitor(
            clips_dir=str(tmp_path),
            merge_window_seconds=0,
            min_significant_seconds=0,
            damping_arrivals_threshold=2,
            damping_window_hours=1,
        )

        for i in range(3):
            confirm_arrival(monitor, ts(i * 60))
        # First two arrivals notify; the third is damped.
        assert len(arrived_notifications(monitor)) == 2

        tick(monitor, ts(180))
        monitor.event_handler.notifications.send_activity_summary.assert_called_once()
        message = monitor.event_handler.notifications.send_activity_summary.call_args[0][0]
        assert "3 visits" in message

        # Two hours later the rate has dropped — notifications resume.
        confirm_arrival(monitor, ts(2 * 3600))
        assert len(arrived_notifications(monitor)) == 3


class TestShutdownFlush:
    def test_flush_writes_held_row(self, tmp_path):
        """A held departure is released immediately at shutdown — no row is
        lost on SIGTERM."""
        monitor = make_monitor(clips_dir=str(tmp_path), merge_window_seconds=300)
        drive_departure(monitor, ts(0), ts(600))
        monitor.event_store.append.assert_not_called()

        # The same wiring run()'s finally block executes:
        monitor._execute_decisions(monitor.significance_filter.flush(ts(650)))

        monitor.event_store.append.assert_called_once()
        row = monitor.event_store.append.call_args[0][0]
        assert row.start_time == ts(0)

    def test_run_finally_flushes_filter(self):
        """Source-level check that run()'s shutdown path flushes the filter."""
        import inspect

        source = inspect.getsource(BufferMonitor.run)
        finally_block = source[source.find("finally:") :]
        assert "significance_filter.flush" in finally_block, (
            "run() must flush held significance-filter decisions on shutdown " "(ho-09 / 025-B)"
        )


class TestConstructionAndConfig:
    def test_constructor_wires_filter(self):
        monitor = make_monitor(
            significance_filter_enabled=True,
            merge_window_seconds=120,
            min_significant_seconds=45,
            damping_arrivals_threshold=6,
            damping_window_hours=2,
        )
        filt = monitor.significance_filter
        assert filt.enabled is True
        assert filt.merge_window_seconds == 120
        assert filt.min_significant_seconds == 45
        assert filt.damping_arrivals_threshold == 6
        assert filt.damping_window_hours == 2

    def test_constructor_default_is_disabled(self):
        """Direct construction keeps today's behavior; production configs
        carry the enabled default via DEFAULTS."""
        with (
            patch("kanyo.detection.buffer_monitor.StreamCapture"),
            patch("kanyo.detection.buffer_monitor.FalconDetector"),
        ):
            monitor = BufferMonitor(stream_url="test")
        assert monitor.significance_filter.enabled is False

    def test_defaults_carry_significance_keys(self):
        assert DEFAULTS["significance_filter_enabled"] is True
        assert DEFAULTS["merge_window_seconds"] == 300
        assert DEFAULTS["min_significant_seconds"] == 30
        assert DEFAULTS["damping_arrivals_threshold"] == 8
        assert DEFAULTS["damping_window_hours"] == 1

    def test_validation_rejects_negative_merge_window(self):
        cfg = {"video_source": "x", "merge_window_seconds": -1}
        with pytest.raises(ValueError, match="merge_window_seconds"):
            _validate(cfg)

    def test_validation_rejects_negative_min_significant(self):
        cfg = {"video_source": "x", "min_significant_seconds": -5}
        with pytest.raises(ValueError, match="min_significant_seconds"):
            _validate(cfg)

    def test_validation_rejects_bad_damping(self):
        with pytest.raises(ValueError, match="damping_arrivals_threshold"):
            _validate({"video_source": "x", "damping_arrivals_threshold": -1})
        with pytest.raises(ValueError, match="damping_window_hours"):
            _validate({"video_source": "x", "damping_window_hours": 0})

    def test_zero_values_accepted(self):
        _validate(
            {
                "video_source": "x",
                "merge_window_seconds": 0,
                "min_significant_seconds": 0,
                "damping_arrivals_threshold": 0,
            }
        )

    def test_short_visit_threshold_deprecation_warning(self, caplog):
        with caplog.at_level("WARNING"):
            _validate({"video_source": "x", "short_visit_threshold": 600})
        assert any("short_visit_threshold is deprecated" in r.message for r in caplog.records)

    def test_no_deprecation_warning_when_key_absent(self, caplog):
        with caplog.at_level("WARNING"):
            _validate({"video_source": "x"})
        assert not any("short_visit_threshold" in r.message for r in caplog.records)


class TestVisitRowFlags:
    def test_to_dict_serializes_new_flags(self):
        visit = FalconVisit(
            start_time=T0,
            end_time=ts(300),
            insignificant=True,
            merged_segments=3,
        )
        d = visit.to_dict()
        assert d["insignificant"] is True
        assert d["merged_segments"] == 3

    def test_flags_default_safe(self):
        visit = FalconVisit(start_time=T0, end_time=ts(300))
        d = visit.to_dict()
        assert d["insignificant"] is False
        assert d["merged_segments"] == 1


class TestTickWiredIntoProcessFrame:
    def test_process_frame_ticks_the_filter(self, tmp_path):
        """A held departure is released by process_frame's per-poll tick."""
        monitor = make_monitor(clips_dir=str(tmp_path), merge_window_seconds=300)
        drive_departure(monitor, ts(0), ts(600))
        monitor.event_store.append.assert_not_called()

        # Quiet frame past the window: no events, tick releases the hold.
        monitor.visit_recorder.is_recording = False
        monitor.arrival_clip_recorder.is_recording.return_value = False
        monitor.frame_buffer.add_frame.return_value = None
        monitor.state_machine.update.return_value = []
        monitor.detector.detect_birds.return_value = []
        frame = MagicMock()
        frame.shape = (720, 1280, 3)

        monitor.process_frame(frame, frame_number=1, timestamp=ts(600 + 301))

        monitor.event_store.append.assert_called_once()
