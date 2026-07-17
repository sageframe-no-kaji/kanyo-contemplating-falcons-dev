"""Tests for StreamCapture connection, lifecycle, and property behavior.

Complements test_stream_reader_thread.py (which owns the reader-thread /
sentinel behavior) with the connect() error paths, the bounded-queue drain
race, video properties, and the context-manager protocol. No network and no
real captures: cv2.VideoCapture and time.sleep are patched throughout.
"""

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from kanyo.detection.capture import BACKOFF_MIN_SECONDS, Frame, StreamCapture

# Non-YouTube URL: connect() uses it directly, no yt-dlp subprocess involved.
STREAM_URL = "http://example.com/test-stream.mp4"


def make_frame_data(value: int = 0) -> np.ndarray:
    """A tiny distinguishable BGR frame."""
    return np.full((4, 4, 3), value, dtype=np.uint8)


class FakePropsCapture:
    """Minimal cv2.VideoCapture stand-in exposing get() properties."""

    def __init__(self, opened: bool = True, props: dict | None = None) -> None:
        self._opened = opened
        self._props = props or {}
        self.released = False

    def isOpened(self) -> bool:
        return self._opened and not self.released

    def read(self):
        return False, None

    def release(self) -> None:
        self.released = True

    def get(self, prop):
        return self._props.get(prop, 0)


class ScriptedCapture(FakePropsCapture):
    """Returns each scripted ndarray once, then fails."""

    def __init__(self, script: list) -> None:
        super().__init__()
        self._script = list(script)

    def read(self):
        if self._script:
            return True, self._script.pop(0)
        return False, None


class StubbornCapture:
    """A capture whose read() ignores release() — a wedged ffmpeg read.

    The read only returns once ``allow_exit`` is set, letting the test
    release the thread after asserting the join-timeout warning.
    """

    def __init__(self, allow_exit: threading.Event) -> None:
        self._allow_exit = allow_exit
        self.released = False

    def isOpened(self) -> bool:
        return True

    def read(self):
        while not self._allow_exit.is_set():
            time.sleep(0.005)
        return False, None

    def release(self) -> None:
        self.released = True


class TestConnectErrorPaths:
    """connect() failure handling beyond the yt-dlp RuntimeError path."""

    def test_unexpected_exception_backs_off_and_returns_false(self):
        """A non-RuntimeError from the capture layer (e.g. an OS error) is
        caught, counted as a failure, and slept through — never raised."""
        capture = StreamCapture(STREAM_URL)

        with patch(
            "kanyo.detection.capture.cv2.VideoCapture",
            side_effect=OSError("device unavailable"),
        ):
            with patch("kanyo.detection.capture.time.sleep") as mock_sleep:
                result = capture.connect()

        assert result is False
        assert capture._consecutive_failures == 1
        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert delay >= BACKOFF_MIN_SECONDS

    def test_use_tee_deprecation_warning(self, caplog):
        """The deprecated use_tee flag is accepted but warns loudly."""
        with caplog.at_level(logging.WARNING, logger="kanyo.detection.capture"):
            StreamCapture(STREAM_URL, use_tee=True)

        assert any("deprecated" in r.message for r in caplog.records)

    def test_frames_raises_when_initial_connect_fails(self):
        """frames() refuses to iterate when the first connect fails."""
        capture = StreamCapture(STREAM_URL)

        with patch.object(capture, "connect", return_value=False):
            gen = capture.frames()
            with pytest.raises(RuntimeError, match="Failed to connect"):
                next(gen)


class TestReadFrame:
    """read_frame() guards and frame counting."""

    def test_read_frame_without_capture_returns_none(self):
        capture = StreamCapture(STREAM_URL)
        assert capture._cap is None
        assert capture.read_frame() is None

    def test_frame_count_tracks_successful_reads(self):
        capture = StreamCapture(STREAM_URL)
        capture._cap = ScriptedCapture([make_frame_data(1), make_frame_data(2)])

        assert capture.frame_count == 0
        f1 = capture.read_frame()
        f2 = capture.read_frame()
        failed = capture.read_frame()

        assert isinstance(f1, Frame) and isinstance(f2, Frame)
        assert failed is None
        assert capture.frame_count == 2  # failed read does not count


class TestEnqueueDrainRace:
    """_enqueue survives the full-then-empty race between put and drop."""

    class RacyQueue:
        """Queue stub: full on first put, empty on drain, then accepts."""

        def __init__(self) -> None:
            self.items: list = []
            self._reject_next_put = True

        def put_nowait(self, item) -> None:
            if self._reject_next_put:
                self._reject_next_put = False
                raise queue.Full
            self.items.append(item)

        def get_nowait(self):
            raise queue.Empty

    def test_enqueue_retries_when_drain_finds_queue_empty(self):
        """queue.Full followed by queue.Empty on the drop (consumer drained
        the queue in between) still lands the item on the retry."""
        capture = StreamCapture(STREAM_URL)
        racy = self.RacyQueue()
        capture._frame_queue = racy  # type: ignore[assignment]

        frame = Frame(
            data=make_frame_data(1),
            frame_number=1,
            width=4,
            height=4,
            timestamp=datetime.now(timezone.utc),
        )
        capture._enqueue(frame)

        assert racy.items == [frame]


class TestReaderThreadLifecycleEdges:
    """Reader-thread start/stop edges not owned by test_stream_reader_thread."""

    def test_start_reader_is_noop_while_thread_alive(self):
        """A second _start_reader() while the reader runs must not spawn a
        replacement thread (which would race on the shared queue)."""
        allow_exit = threading.Event()
        fake = StubbornCapture(allow_exit)
        capture = StreamCapture(STREAM_URL)

        try:
            with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
                assert capture.connect() is True
            capture._start_reader()
            first_thread = capture._reader_thread
            capture._start_reader()
            assert capture._reader_thread is first_thread
        finally:
            allow_exit.set()
            capture.disconnect()

    def test_disconnect_warns_when_reader_ignores_join_timeout(self, caplog):
        """A reader wedged in cap.read() past the join timeout is logged
        loudly; disconnect() still returns instead of hanging."""
        allow_exit = threading.Event()
        fake = StubbornCapture(allow_exit)
        capture = StreamCapture(STREAM_URL)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            assert capture.connect() is True
        capture._start_reader()
        thread = capture._reader_thread
        assert thread is not None and thread.is_alive()

        try:
            with patch("kanyo.detection.capture.READER_JOIN_TIMEOUT_S", 0.05):
                with caplog.at_level(logging.WARNING, logger="kanyo.detection.capture"):
                    capture.disconnect()

            assert any("did not stop" in r.message for r in caplog.records)
            assert capture._reader_thread is None
            assert thread.is_alive()  # genuinely wedged past the timeout
        finally:
            allow_exit.set()
            thread.join(timeout=2.0)
        assert not thread.is_alive()


class TestVideoProperties:
    """total_frames / fps come from the capture, with disconnected defaults."""

    def test_properties_default_to_zero_when_disconnected(self):
        capture = StreamCapture(STREAM_URL)
        assert capture.total_frames == 0
        assert capture.fps == 0.0

    def test_properties_read_from_capture(self):
        capture = StreamCapture(STREAM_URL)
        capture._cap = FakePropsCapture(
            props={cv2.CAP_PROP_FRAME_COUNT: 250.0, cv2.CAP_PROP_FPS: 30.0}
        )
        assert capture.total_frames == 250
        assert capture.fps == 30.0


class TestContextManager:
    """with StreamCapture(...) connects on enter and disconnects on exit."""

    def test_enter_connects_and_exit_disconnects(self):
        fake = FakePropsCapture(opened=True)
        capture = StreamCapture(STREAM_URL)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            with capture as entered:
                assert entered is capture
                assert capture.is_connected is True

        assert capture._cap is None
        assert fake.released is True
