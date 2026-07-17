"""Behavior tests for the buffer-based monitor (BufferMonitor).

Covers the orchestration surfaces the 021-D regression file does not touch:
frame-processing side branches, departure-candidate lifecycle (022-C),
startup/arrival cancellation cleanup, roosting-stop departures with
string-typed metadata timestamps, the run() loop (init phase, frame-skip
path, outage sentinels, shutdown), and the main() CLI entry.

Nothing real runs: capture is a scripted fake, the detector and recorders
are mocks, the module clock is a controllable FakeTime, and the hardware
encoder cache is pre-seeded so no ffmpeg probe fires.
"""

import signal
from concurrent.futures import Future
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

import kanyo.detection.buffer_monitor as bm
import kanyo.utils.encoder as encoder
from kanyo.detection.event_types import FalconEvent
from kanyo.detection.falcon_state import FalconState

BASE = datetime(2026, 1, 1, 12, 0, 0)

FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


class IsoTimestamp(str):
    """ISO-8601 string that also answers strftime.

    The state-machine metadata contract allows string timestamps
    (buffer_monitor parses them defensively); FalconVisit id generation
    needs strftime, so this models a str-typed timestamp faithfully.
    """

    def strftime(self, fmt: str) -> str:
        return datetime.fromisoformat(self).strftime(fmt)


class _Det:
    """Minimal detection: only .confidence is inspected by the monitor."""

    def __init__(self, confidence: float = 0.9):
        self.confidence = confidence


class FakeTime:
    """Controllable stand-in for the module's ``time`` import."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeCapture:
    """Scripted stand-in for StreamCapture: frames() is a test generator."""

    def __init__(self, gen_factory):
        self._gen_factory = gen_factory
        self.disconnected = False
        self.on_connection_issue = None

    def frames(self, skip=0):
        return self._gen_factory()

    def disconnect(self):
        self.disconnected = True


def _frame(number: int, timestamp: datetime) -> SimpleNamespace:
    return SimpleNamespace(data=FRAME, frame_number=number, timestamp=timestamp)


def _mock_visit_recorder(recording: bool = False) -> MagicMock:
    vr = MagicMock()
    vr.is_recording = recording
    vr.stream_outage_exceeded = False
    vr.lead_in_seconds = 15
    vr.get_temp_path.return_value = None
    vr.stop_recording.return_value = (None, {})
    return vr


def _mock_arrival_recorder(recording: bool = False) -> MagicMock:
    ar = MagicMock()
    ar.is_recording.return_value = recording
    ar.get_temp_path.return_value = None
    return ar


def _raising_path(name: str = "stuck.tmp") -> MagicMock:
    """A Path-like whose unlink always fails with OSError."""
    p = MagicMock()
    p.exists.return_value = True
    p.unlink.side_effect = OSError("permission denied")
    p.name = name
    return p


def make_monitor(tmp_path, **kwargs) -> bm.BufferMonitor:
    """Construct a BufferMonitor and replace heavy collaborators with mocks."""
    kwargs.setdefault("presence_enabled", False)
    kwargs.setdefault("clips_dir", str(tmp_path / "clips"))
    kwargs.setdefault("full_config", {})
    monitor = bm.BufferMonitor(stream_url="test://stream", **kwargs)

    # Real clip manager holds a ThreadPoolExecutor — shut it down and mock.
    monitor.clip_manager.shutdown()
    monitor.clip_manager = MagicMock()
    monitor.clip_manager.clip_departure_before = 30
    monitor.clip_manager.clip_departure_after = 15

    monitor.detector = MagicMock()
    monitor.detector.detect_birds.return_value = []
    monitor.detector.detect_with_raw.return_value = ([], [])

    monitor.visit_recorder = _mock_visit_recorder()
    monitor.arrival_clip_recorder = _mock_arrival_recorder()

    monitor.event_handler = MagicMock()
    monitor.event_handler.notifications = None
    monitor.event_store = MagicMock()
    return monitor


@pytest.fixture(autouse=True)
def _no_ffmpeg_probe(monkeypatch):
    """Pre-seed the hardware-encoder cache so nothing probes ffmpeg."""
    monkeypatch.setattr(encoder, "_detected_encoder", "libx264", raising=False)


@pytest.fixture(autouse=True)
def _clean_shutdown_flag(monkeypatch):
    """Keep the module-global SIGTERM flag isolated per test."""
    monkeypatch.setattr(bm, "_shutdown_requested", False)


class TestSigtermHandler:
    def test_sigterm_sets_shutdown_flag(self):
        assert bm._shutdown_requested is False
        bm._handle_sigterm(signal.SIGTERM, None)
        assert bm._shutdown_requested is True


class TestProcessFrameBranches:
    def test_active_arrival_clip_receives_every_processed_frame(self, tmp_path):
        """While the arrival clip records, each processed frame is written to it."""
        monitor = make_monitor(tmp_path)
        monitor.state_machine = MagicMock()
        monitor.state_machine.update.return_value = []
        monitor.arrival_clip_recorder.is_recording.return_value = True

        monitor.process_frame(FRAME, 1, BASE)

        monitor.arrival_clip_recorder.write_frame.assert_called_once_with(FRAME, BASE)

    def test_roosting_stop_mode_skips_yolo_within_interval(self, tmp_path):
        """In roosting stop mode, YOLO polls run only every roosting_detection_interval."""
        monitor = make_monitor(tmp_path)
        monitor.roosting_mode_active = True
        monitor.roosting_recording_mode = "stop"
        monitor.roosting_detection_interval = 30
        monitor.last_roosting_check = BASE

        monitor.process_frame(FRAME, 1, BASE + timedelta(seconds=5))

        monitor.detector.detect_birds.assert_not_called()
        # The reduced-interval clock did not advance on a skipped poll
        assert monitor.last_roosting_check == BASE

    def test_detection_marks_visit_recorder_while_recording(self, tmp_path):
        """A detected frame during an active recording marks the detection offset."""
        monitor = make_monitor(tmp_path)
        monitor.state_machine = MagicMock()
        monitor.state_machine.update.return_value = []
        monitor.visit_recorder.is_recording = True
        monitor.detector.detect_birds.return_value = [_Det(0.8)]

        monitor.process_frame(FRAME, 1, BASE)

        monitor.visit_recorder.mark_detection.assert_called_once()
        assert monitor.last_detection_time == BASE

    def test_processing_error_is_swallowed_and_logged(self, tmp_path):
        """A failure inside frame processing never propagates to the run loop."""
        monitor = make_monitor(tmp_path)
        monitor.frame_buffer = MagicMock()
        monitor.frame_buffer.add_frame.side_effect = RuntimeError("boom")

        monitor.process_frame(FRAME, 1, BASE)  # must not raise

        monitor.detector.detect_birds.assert_not_called()

    def test_startup_cancelled_when_ratio_below_threshold(self, tmp_path):
        """A failed startup confirmation resets state and deletes .tmp recordings."""
        monitor = make_monitor(
            tmp_path,
            full_config={
                "arrival_confirmation_seconds": 5,
                "arrival_confirmation_ratio": 0.5,
            },
        )
        monitor.state_machine = MagicMock()
        monitor.state_machine.update.return_value = []

        arrival_tmp = tmp_path / "arrival.mp4.tmp"
        arrival_tmp.write_bytes(b"x")
        monitor.arrival_clip_recorder.get_temp_path.return_value = arrival_tmp
        visit_tmp = _raising_path("visit.mp4.tmp")
        monitor.visit_recorder.get_temp_path.return_value = visit_tmp

        monitor.startup_pending = True
        monitor.startup_pending_start = BASE
        monitor.startup_detection_count = 0
        monitor.startup_frame_count = 4

        # Window elapsed, no detections in it: 0/5 < 0.5 → cancel
        monitor.process_frame(FRAME, 1, BASE + timedelta(seconds=6))

        assert monitor.startup_pending is False
        assert monitor.startup_frame_count == 0
        monitor.state_machine.reset_to_absent.assert_called_once()
        # .tmp cleanup: the deletable file is gone, the stuck one only logged
        assert not arrival_tmp.exists()
        visit_tmp.unlink.assert_called_once()


class TestDepartureCandidate:
    def test_snapshot_without_last_detection_is_refused(self, tmp_path):
        monitor = make_monitor(tmp_path)
        monitor.state_machine = MagicMock()
        monitor.state_machine.last_detection = None

        monitor._snapshot_departure_candidate(BASE)

        assert monitor._departure_candidate is None
        monitor.clip_manager.extract_candidate_clip.assert_not_called()

    def test_snapshot_discards_stale_candidate_first(self, tmp_path):
        """A leftover candidate is discarded before a new one is snapshotted."""
        monitor = make_monitor(tmp_path)
        monitor.state_machine = MagicMock()
        monitor.state_machine.last_detection = BASE

        stale_future: Future = Future()
        stale_future.set_result(None)
        stale_tmp = MagicMock()
        stale_tmp.exists.return_value = False
        monitor._departure_candidate = (stale_future, stale_tmp, tmp_path / "old.mp4")

        new_future: Future = Future()
        monitor.clip_manager.extract_candidate_clip.return_value = new_future

        monitor._snapshot_departure_candidate(BASE + timedelta(seconds=60))

        assert monitor._departure_candidate is not None
        assert monitor._departure_candidate[0] is new_future
        monitor.clip_manager.extract_candidate_clip.assert_called_once()

    def test_discard_tolerates_failed_extraction(self, tmp_path):
        monitor = make_monitor(tmp_path)
        failed: Future = Future()
        failed.set_exception(RuntimeError("extraction died"))
        tmp = MagicMock()
        tmp.exists.return_value = False
        monitor._departure_candidate = (failed, tmp, tmp_path / "final.mp4")

        monitor._discard_departure_candidate()  # must not raise

        assert monitor._departure_candidate is None
        tmp.unlink.assert_not_called()

    def test_discard_logs_when_tmp_cannot_be_deleted(self, tmp_path):
        monitor = make_monitor(tmp_path)
        done: Future = Future()
        done.set_result("clip.mp4")
        tmp = _raising_path("cand.mp4.tmp")
        monitor._departure_candidate = (done, tmp, tmp_path / "final.mp4")

        monitor._discard_departure_candidate()  # OSError swallowed with warning

        assert monitor._departure_candidate is None
        tmp.unlink.assert_called_once()

    def test_discard_deletes_existing_tmp_file(self, tmp_path):
        monitor = make_monitor(tmp_path)
        tmp = tmp_path / "cand.mp4.tmp"
        tmp.write_bytes(b"video")
        done: Future = Future()
        done.set_result(str(tmp))
        monitor._departure_candidate = (done, tmp, tmp_path / "final.mp4")

        monitor._discard_departure_candidate()

        assert monitor._departure_candidate is None
        assert not tmp.exists()

    def test_finalize_returns_false_when_extraction_failed(self, tmp_path):
        monitor = make_monitor(tmp_path)
        failed: Future = Future()
        failed.set_exception(RuntimeError("extraction died"))
        tmp = MagicMock()
        tmp.exists.return_value = False
        monitor._departure_candidate = (failed, tmp, tmp_path / "final.mp4")

        assert monitor._finalize_departure_candidate(BASE) is False
        assert monitor._departure_candidate is None

    def test_finalize_returns_false_when_rename_fails(self, tmp_path):
        monitor = make_monitor(tmp_path)
        done: Future = Future()
        done.set_result("clip.mp4")
        tmp = MagicMock()
        tmp.exists.return_value = True
        tmp.rename.side_effect = OSError("cross-device link")
        tmp.name = "cand.mp4.tmp"
        monitor._departure_candidate = (done, tmp, tmp_path / "final.mp4")

        assert monitor._finalize_departure_candidate(BASE) is False

    def test_finalize_renames_candidate_to_final(self, tmp_path):
        monitor = make_monitor(tmp_path)
        tmp = tmp_path / "cand.mp4.tmp"
        tmp.write_bytes(b"video")
        final = tmp_path / "cand.mp4"
        done: Future = Future()
        done.set_result(str(tmp))
        monitor._departure_candidate = (done, tmp, final)

        assert monitor._finalize_departure_candidate(BASE) is True
        assert final.exists()
        assert not tmp.exists()


class TestDepartedEvent:
    def test_departure_during_pending_arrival_cancels_instead(self, tmp_path):
        """DEPARTED while arrival is unconfirmed cancels the arrival, cleans .tmp files."""
        monitor = make_monitor(tmp_path)
        monitor.state_machine = MagicMock()
        monitor.arrival_pending = True
        monitor.arrival_pending_start = BASE
        monitor.arrival_detection_count = 1
        monitor.arrival_frame_count = 10

        arrival_tmp = tmp_path / "arrival.mp4.tmp"
        arrival_tmp.write_bytes(b"x")
        monitor.arrival_clip_recorder.get_temp_path.return_value = arrival_tmp
        visit_tmp = _raising_path("visit.mp4.tmp")
        monitor.visit_recorder.get_temp_path.return_value = visit_tmp

        monitor._handle_event(FalconEvent.DEPARTED, BASE + timedelta(seconds=8), {})

        assert monitor.arrival_pending is False
        monitor.state_machine.reset_to_absent.assert_called_once()
        assert not arrival_tmp.exists()  # deletable .tmp removed
        visit_tmp.unlink.assert_called_once()  # stuck .tmp only logged
        # The departure was NOT surfaced as a real visit
        monitor.event_store.upsert.assert_not_called()
        monitor.event_handler.handle_event.assert_not_called()

    def test_roosting_stop_departure_with_string_timestamps(self, tmp_path):
        """A roosting-stop departure finalizes the candidate and records the visit,
        parsing str-typed visit_start/visit_end metadata."""
        monitor = make_monitor(tmp_path)
        monitor.roosting_mode_active = True
        monitor.roosting_recording_mode = "stop"
        monitor._roosting_visit_metadata = {"visit_file": "roost.mp4"}
        monitor._visit_peak_confidence = 0.77

        cand_tmp = tmp_path / "cand.mp4.tmp"
        cand_tmp.write_bytes(b"video")
        cand_final = tmp_path / "cand.mp4"
        done: Future = Future()
        done.set_result(str(cand_tmp))
        monitor._departure_candidate = (done, cand_tmp, cand_final)

        metadata = {
            "visit_start": IsoTimestamp("2026-01-01T10:00:00"),
            "visit_end": IsoTimestamp("2026-01-01T11:00:00"),
        }
        monitor._frame_now = BASE
        monitor._handle_event(FalconEvent.DEPARTED, BASE, metadata)

        assert cand_final.exists()
        assert monitor.roosting_mode_active is False
        assert monitor._roosting_visit_metadata is None
        row = monitor.event_store.upsert.call_args[0][0]
        assert row.peak_confidence == 0.77
        assert row.departure_clip_path is not None
        monitor.event_handler.handle_event.assert_called_once()
        assert monitor._visit_peak_confidence == 0.0  # reset for next visit

    def test_normal_departure_parses_string_visit_start(self, tmp_path):
        """A normal departure with str-typed visit_start still records a full row."""
        monitor = make_monitor(tmp_path)
        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stop_recording.return_value = (
            "visit.mp4",
            {"duration_seconds": 42.0},
        )
        monitor.clip_manager.create_departure_clip.return_value = True
        monitor._visit_peak_confidence = 0.66

        metadata = {
            "visit_start": IsoTimestamp("2026-01-01T10:00:00"),
            "visit_end": BASE,
        }
        monitor._frame_now = BASE
        monitor._handle_event(FalconEvent.DEPARTED, BASE, metadata)

        row = monitor.event_store.upsert.call_args[0][0]
        assert row.peak_confidence == 0.66
        assert row.departure_clip_path is not None
        monitor.clip_manager.create_departure_clip.assert_called_once()


class TestContinuationArrivalClip:
    def test_discard_logs_when_file_cannot_be_deleted(self, tmp_path):
        monitor = make_monitor(tmp_path)
        monitor.arrival_pending_start = BASE
        stuck = _raising_path("arrival.mp4.tmp")
        monitor.arrival_clip_recorder.get_temp_path.return_value = stuck
        monitor.arrival_clip_recorder.is_recording.return_value = False

        monitor._discard_continuation_arrival_clip(BASE)  # OSError swallowed

        stuck.unlink.assert_called_once()


class TestStartupConfirmation:
    def test_confirm_without_startup_notification(self, tmp_path):
        """notify_on_startup=False confirms presence silently."""
        monitor = make_monitor(tmp_path, notify_on_startup=False)
        monitor.state_machine = MagicMock()
        monitor.startup_pending = True
        monitor.startup_pending_start = BASE
        monitor.startup_detection_count = 3
        monitor.startup_frame_count = 5

        monitor._confirm_startup_presence(BASE + timedelta(seconds=10))

        monitor.state_machine.confirm_startup_presence.assert_called_once()
        monitor.event_handler.handle_event.assert_not_called()
        assert monitor.startup_pending is False
        assert monitor.startup_frame_count == 0


class TestRunLoop:
    def test_full_startup_cycle_to_roosting(self, tmp_path, monkeypatch):
        """Init with a bird → PENDING_STARTUP with startup arrival clip →
        confirmation → ROOSTING; heartbeat and max-runtime exit; clean shutdown."""
        fake_time = FakeTime(1000.0)
        monkeypatch.setattr(bm, "time", fake_time)

        monitor = make_monitor(
            tmp_path,
            presence_enabled=True,
            process_interval_frames=1,
            record_arrival_on_startup=True,
            max_runtime_seconds=500,
            full_config={
                "arrival_confirmation_seconds": 5,
                "arrival_confirmation_ratio": 0.3,
            },
        )
        monitor.presence = MagicMock()
        monitor.presence.update.return_value = True
        monitor.detector.detect_birds.return_value = [_Det(0.9)]
        monitor.detector.detect_with_raw.return_value = ([_Det(0.9)], [_Det(0.9)])
        monitor.visit_recorder.is_recording = True
        monitor.visit_recorder.stop_recording.return_value = (
            "visit.mp4",
            {"duration_seconds": 9.0},
        )
        monitor.arrival_clip_recorder.is_recording.return_value = True

        def gen():
            # Three init frames (elapsed < 30s)
            for i in range(3):
                yield _frame(i, BASE + timedelta(seconds=i))
            # Init window over: next frame completes initialization
            fake_time.now = 1031.0
            yield _frame(3, BASE + timedelta(seconds=31))
            yield _frame(4, BASE + timedelta(seconds=33))
            # Heartbeat due (>=300s since loop start), confirmation window over
            fake_time.now = 1400.0
            yield _frame(5, BASE + timedelta(seconds=38))
            # Max runtime reached after this frame
            fake_time.now = 1600.0
            yield _frame(6, BASE + timedelta(seconds=40))

        monitor.capture = FakeCapture(gen)
        monitor.run()

        # Startup presence confirmed: real state machine reached ROOSTING
        assert monitor.state_machine.state == FalconState.ROOSTING
        # Startup arrival clip was recorded (record_arrival_on_startup=True)
        monitor.arrival_clip_recorder.start_recording.assert_called_once()
        # Presence tracker was seeded from the init detections
        monitor.presence.update.assert_called()
        # Startup notification flowed through the filter to the handler
        arrived_calls = [
            c
            for c in monitor.event_handler.handle_event.call_args_list
            if c.args[0] == FalconEvent.ARRIVED
        ]
        assert len(arrived_calls) == 1
        # Shutdown path: visit + arrival clip finalized, capture disconnected
        monitor.visit_recorder.rename_to_final.assert_called()
        monitor.arrival_clip_recorder.rename_to_final.assert_called()
        monitor.clip_manager.shutdown.assert_called_once()
        assert monitor.capture.disconnected is True

    def test_startup_without_arrival_clip(self, tmp_path, monkeypatch):
        """record_arrival_on_startup=False skips the startup arrival clip but
        still starts the visit recording."""
        fake_time = FakeTime(1000.0)
        monkeypatch.setattr(bm, "time", fake_time)

        monitor = make_monitor(
            tmp_path,
            process_interval_frames=1,
            record_arrival_on_startup=False,
        )
        monitor.detector.detect_birds.return_value = [_Det(0.8)]

        def gen():
            yield _frame(0, BASE)
            fake_time.now = 1031.0
            yield _frame(1, BASE + timedelta(seconds=31))

        monitor.capture = FakeCapture(gen)
        monitor.run()

        monitor.arrival_clip_recorder.start_recording.assert_not_called()
        monitor.visit_recorder.start_recording.assert_called_once()
        assert monitor.startup_pending is True
        assert monitor.state_machine.state == FalconState.PENDING_STARTUP

    def test_skip_path_outage_reset_and_keyboard_interrupt(self, tmp_path, monkeypatch):
        """Skipped frames still feed the recorders; an exceeded outage on a
        skipped frame resets state; Ctrl+C exits cleanly."""
        fake_time = FakeTime(1000.0)
        monkeypatch.setattr(bm, "time", fake_time)

        monitor = make_monitor(tmp_path, process_interval_frames=3)
        vr = monitor.visit_recorder
        ar = monitor.arrival_clip_recorder

        def gen():
            # Empty init window: first frame completes init to ABSENT, then
            # (counter=1) hits the skip path with an exceeded outage.
            fake_time.now = 1031.0
            vr.is_recording = True
            vr.stream_outage_exceeded = True
            yield _frame(1, BASE + timedelta(seconds=31))
            # Next skipped frame feeds only the active arrival clip
            vr.is_recording = False
            ar.is_recording.return_value = True
            yield _frame(2, BASE + timedelta(seconds=32))
            # counter=3 → this frame is processed
            ar.is_recording.return_value = False
            yield _frame(3, BASE + timedelta(seconds=33))
            raise KeyboardInterrupt

        monitor.capture = FakeCapture(gen)
        monitor.run()  # KeyboardInterrupt handled inside

        # Outage-exceeded on the skip path stopped both recorders
        vr.stop_recording.assert_called()
        assert monitor.state_machine.state == FalconState.ABSENT
        # The second skipped frame was written to the arrival clip
        ar.write_frame.assert_called_once()
        # The third frame went through detection
        monitor.detector.detect_birds.assert_called_once()
        assert monitor.capture.disconnected is True

    def test_sigterm_breaks_run_loop(self, tmp_path, monkeypatch):
        """The SIGTERM flag set by the handler breaks the loop before processing."""
        fake_time = FakeTime(1000.0)
        monkeypatch.setattr(bm, "time", fake_time)

        monitor = make_monitor(tmp_path)
        bm._handle_sigterm(signal.SIGTERM, None)
        assert bm._shutdown_requested is True

        def gen():
            yield _frame(1, BASE)
            raise AssertionError("loop must break before requesting a second frame")

        monitor.capture = FakeCapture(gen)
        monitor.run()

        monitor.detector.detect_birds.assert_not_called()
        assert monitor.capture.disconnected is True

    def test_outage_during_init_is_dropped(self, tmp_path, monkeypatch):
        """A no-frame sentinel during startup init clears without recovery logic."""
        fake_time = FakeTime(1000.0)
        monkeypatch.setattr(bm, "time", fake_time)

        monitor = make_monitor(tmp_path)

        def gen():
            yield None  # sentinel: outage begins
            yield _frame(1, BASE)  # first real frame, still initializing

        monitor.capture = FakeCapture(gen)
        monitor.run()

        assert monitor._outage_start is None
        assert monitor._outage_sentinel_count == 0
        # No recovery confirmation was started
        assert monitor.recovery_pending is False


class TestMain:
    @pytest.fixture
    def main_env(self, monkeypatch):
        cfg: dict = {}
        load_config = MagicMock(return_value=cfg)
        monkeypatch.setattr(bm, "load_config", load_config)
        monkeypatch.setattr(bm, "setup_logging_from_config", MagicMock())
        nm_instance = MagicMock()
        monkeypatch.setattr(bm, "NotificationManager", MagicMock(return_value=nm_instance))
        monitor = MagicMock()
        monitor_cls = MagicMock(return_value=monitor)
        monkeypatch.setattr(bm, "BufferMonitor", monitor_cls)
        return SimpleNamespace(
            cfg=cfg,
            load_config=load_config,
            monitor=monitor,
            monitor_cls=monitor_cls,
            nm=nm_instance,
        )

    def test_default_config_builds_and_runs_monitor(self, main_env, monkeypatch):
        monkeypatch.setattr("sys.argv", ["buffer_monitor.py"])

        bm.main()

        main_env.load_config.assert_called_once_with("config.yaml")
        main_env.monitor.run.assert_called_once()
        # The connection-issue alert closure routes to the admin alert channel
        alert = main_env.monitor.capture.on_connection_issue
        alert("stream down")
        main_env.monitor.event_handler.notifications.send_system_alert.assert_called_once_with(
            "stream down"
        )

    def test_harvard_flag_and_duration(self, main_env, monkeypatch):
        monkeypatch.setattr("sys.argv", ["buffer_monitor.py", "--harvard", "--duration", "2"])

        bm.main()

        main_env.load_config.assert_called_once_with("test_config_harvard.yaml")
        assert main_env.cfg["max_runtime_seconds"] == 120
        assert main_env.monitor_cls.call_args.kwargs["max_runtime_seconds"] == 120

    def test_nsw_flag(self, main_env, monkeypatch):
        monkeypatch.setattr("sys.argv", ["buffer_monitor.py", "--nsw"])

        bm.main()

        main_env.load_config.assert_called_once_with("test_config_nsw.yaml")

    def test_keyboard_interrupt_is_handled(self, main_env, monkeypatch):
        monkeypatch.setattr("sys.argv", ["buffer_monitor.py"])
        main_env.monitor.run.side_effect = KeyboardInterrupt

        bm.main()  # must not raise

    def test_fatal_error_is_reraised(self, main_env, monkeypatch):
        monkeypatch.setattr("sys.argv", ["buffer_monitor.py"])
        main_env.monitor.run.side_effect = RuntimeError("fatal")

        with pytest.raises(RuntimeError, match="fatal"):
            bm.main()
