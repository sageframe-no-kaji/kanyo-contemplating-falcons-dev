"""Tests for the worker-thread frame reader with timeout sentinel (ho-11 / 023-A).

A scriptable fake cv2.VideoCapture drives the reader thread through frames,
blocked reads, silence, and read failures. All waits are short polling loops
bounded by the fake's ``release()`` — no sleep-based flakiness, no deadlocks.
"""

import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np

from kanyo.detection.capture import Frame, StreamCapture

# Non-YouTube URL: connect() uses it directly, no yt-dlp subprocess involved.
STREAM_URL = "http://example.com/test-stream.mp4"

# Short read timeout so sentinel tests run fast; generous enough to be
# deterministic (the worker pushes frames within microseconds).
READ_TIMEOUT_S = 0.05


def make_frame_data(value: int = 0) -> np.ndarray:
    """A tiny distinguishable BGR frame."""
    return np.full((4, 4, 3), value, dtype=np.uint8)


class FakeVideoCapture:
    """Scriptable stand-in for cv2.VideoCapture.

    Script items:
    - ``np.ndarray`` — returned as a successful read
    - ``"fail"`` — read returns (False, None), like a broken stream
    - ``threading.Event`` — read blocks until the event is set (a stalled
      stream whose read never returns)

    An exhausted script blocks forever (live-stream silence). ``release()``
    unblocks any waiting read and makes it return failure — mirroring how a
    released cv2 capture behaves.
    """

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self._lock = threading.Lock()
        self.released = False

    def isOpened(self) -> bool:
        return not self.released

    def read(self):
        while True:
            if self.released:
                return False, None

            with self._lock:
                item = self._script[0] if self._script else None

            if item is None:
                # Script exhausted — silent stream, block until released
                time.sleep(0.005)
                continue

            if isinstance(item, threading.Event):
                if not item.is_set():
                    time.sleep(0.005)
                    continue
                with self._lock:
                    self._script.pop(0)
                continue

            with self._lock:
                self._script.pop(0)

            if isinstance(item, str) and item == "fail":
                return False, None
            return True, item

    def release(self) -> None:
        self.released = True


def reader_threads() -> list[threading.Thread]:
    """All live stream-reader threads."""
    return [t for t in threading.enumerate() if t.name == "stream-reader" and t.is_alive()]


def next_real_frame(gen, max_sentinels: int = 200) -> Frame:
    """Advance the generator past None sentinels to the next real frame."""
    for _ in range(max_sentinels):
        item = next(gen)
        if item is not None:
            return item
    raise AssertionError("No real frame within sentinel budget")


class TestNoFrameSentinel:
    """frames() yields None when no frame arrives within read_timeout_s."""

    def test_silence_yields_none_sentinel(self):
        """A source that stops returning frames produces None sentinels."""
        fake = FakeVideoCapture([make_frame_data(1), make_frame_data(2)])
        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            gen = capture.frames()
            f1 = next(gen)
            f2 = next(gen)
            sentinel = next(gen)
            gen.close()

        assert isinstance(f1, Frame) and f1.frame_number == 1
        assert isinstance(f2, Frame) and f2.frame_number == 2
        assert sentinel is None

    def test_blocked_read_yields_none_sentinel(self):
        """A read that BLOCKS (not merely fails) still surfaces as a sentinel.

        This is the 021-F gap: a blocked cv2.read() froze the whole pipeline
        silently. With the reader thread, a blocked read is just a quiet
        queue and the consumer timeout sees it.
        """
        blocker = threading.Event()  # never set: read blocks indefinitely
        fake = FakeVideoCapture([make_frame_data(1), blocker])
        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            gen = capture.frames()
            f1 = next(gen)
            sentinel = next(gen)
            gen.close()  # disconnect() releases the fake, unblocking the read

        assert isinstance(f1, Frame)
        assert sentinel is None
        assert reader_threads() == []

    def test_frames_resume_after_stall(self):
        """When a stalled read unblocks, real frames flow again."""
        stall = threading.Event()
        fake = FakeVideoCapture([make_frame_data(1), stall, make_frame_data(2)])
        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            gen = capture.frames()
            f1 = next(gen)
            sentinel = next(gen)
            stall.set()
            f2 = next_real_frame(gen)
            gen.close()

        assert isinstance(f1, Frame) and f1.frame_number == 1
        assert sentinel is None
        assert isinstance(f2, Frame) and f2.frame_number == 2


class TestReadFailureReconnect:
    """ret == False runs the existing lost-alert + reconnect path."""

    def test_read_failure_reconnects_and_resumes(self):
        """A failed read triggers one reconnect; frames resume on the new capture."""
        fake1 = FakeVideoCapture([make_frame_data(1), "fail"])
        fake2 = FakeVideoCapture([make_frame_data(2)])
        messages: list[str] = []

        capture = StreamCapture(
            STREAM_URL,
            read_timeout_s=READ_TIMEOUT_S,
            on_connection_issue=messages.append,
        )
        capture._last_admin_notification_time = 0  # outside the 1/hour throttle

        with patch("kanyo.detection.capture.cv2.VideoCapture", side_effect=[fake1, fake2]):
            gen = capture.frames()
            f1 = next(gen)
            f2 = next_real_frame(gen)
            gen.close()

        assert isinstance(f1, Frame) and f1.frame_number == 1
        # Frame numbering continues across the reconnect
        assert isinstance(f2, Frame) and f2.frame_number == 2
        assert fake1.released is True
        # Paired alerts: one lost, one reconnected (022-D gating intact)
        assert len(messages) == 2
        assert "connection lost" in messages[0].lower()
        assert "reconnected" in messages[1].lower()
        # Successful reconnect resets backoff state
        assert capture._consecutive_failures == 0

    def test_failed_reconnect_yields_sentinel_and_retries(self):
        """While reconnection fails, the consumer sees sentinels, then frames
        once a connection succeeds. Backoff stays inside connect()."""
        fake1 = FakeVideoCapture(["fail"])
        fake_closed = FakeVideoCapture([])
        fake_closed.released = True  # isOpened() False -> connect() fails
        fake2 = FakeVideoCapture([make_frame_data(7)])

        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        with patch(
            "kanyo.detection.capture.cv2.VideoCapture",
            side_effect=[fake1, fake_closed, fake2],
        ):
            with patch("kanyo.detection.capture.time.sleep") as mock_sleep:
                gen = capture.frames()
                sentinel = next(gen)  # read failure -> reconnect fails -> sentinel
                frame = next_real_frame(gen)  # second reconnect attempt succeeds
                gen.close()

        assert sentinel is None
        assert isinstance(frame, Frame)
        # The failed connect slept its backoff (connect() owns retry timing)
        assert mock_sleep.called
        assert capture._consecutive_failures == 0


class TestReadTimeTimestamps:
    """Frame.timestamp comes from the injected clock, at read time."""

    def test_timestamps_come_from_injected_now_fn(self):
        """Yielded frames carry the injected clock's stamps in read order."""
        t0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
        stamps = [t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2)]
        clock = iter(stamps)

        fake = FakeVideoCapture([make_frame_data(i) for i in range(3)])
        capture = StreamCapture(
            STREAM_URL,
            read_timeout_s=READ_TIMEOUT_S,
            now_fn=lambda: next(clock),
        )

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            gen = capture.frames()
            frames = [next(gen) for _ in range(3)]
            gen.close()

        assert [f.timestamp for f in frames] == stamps

    def test_timestamps_at_read_cadence_not_consumer_cadence(self):
        """A slow consumer still sees read-time stamps: the reader stamps all
        frames as it reads them into the queue, before the consumer drains
        them. This is the anti-burst-stamping property."""
        t0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
        read_times: list[datetime] = []

        def clock() -> datetime:
            stamp = t0 + timedelta(seconds=len(read_times))
            read_times.append(stamp)
            return stamp

        fake = FakeVideoCapture([make_frame_data(i) for i in range(3)])
        capture = StreamCapture(STREAM_URL, read_timeout_s=1.0, now_fn=clock)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            gen = capture.frames()
            first = next(gen)
            # Wait until the reader has stamped ALL frames (they queue up
            # while the consumer sits idle — a simulated pipeline stall).
            deadline = time.time() + 5.0
            while len(read_times) < 3 and time.time() < deadline:
                time.sleep(0.005)
            drained = [next(gen) for _ in range(2)]
            gen.close()

        stamps = [first.timestamp] + [f.timestamp for f in drained]
        # Monotonic 1s spacing from the reader clock, not near-identical
        # consumer-time stamps.
        assert stamps == [t0 + timedelta(seconds=i) for i in range(3)]

    def test_default_clock_is_utc(self):
        """Without an injected clock, frames are stamped with UTC wall time."""
        fake = FakeVideoCapture([make_frame_data(1)])
        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            gen = capture.frames()
            frame = next(gen)
            gen.close()

        assert frame.timestamp.tzinfo == timezone.utc


class TestThreadLifecycle:
    """The reader thread never leaks across disconnects or reconnect cycles."""

    def test_disconnect_stops_reader_thread(self):
        fake = FakeVideoCapture([make_frame_data(1)])
        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            assert capture.connect() is True
            capture._start_reader()
            assert reader_threads() != []
            capture.disconnect()

        assert reader_threads() == []
        assert capture._reader_thread is None

    def test_repeated_cycles_accumulate_no_threads(self):
        """connect/disconnect cycles never accumulate reader threads."""
        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        for i in range(3):
            fake = FakeVideoCapture([make_frame_data(i)])
            with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
                assert capture.connect() is True
                capture._start_reader()
                assert len(reader_threads()) == 1
                capture.disconnect()
            assert reader_threads() == []

    def test_generator_close_stops_reader_thread(self):
        """Closing the frames() generator disconnects and joins the reader."""
        fake = FakeVideoCapture([make_frame_data(1)])
        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            gen = capture.frames()
            next(gen)
            assert reader_threads() != []
            gen.close()

        assert reader_threads() == []


class TestBoundedQueue:
    """The reader queue is bounded and drops the oldest item when full."""

    def test_enqueue_drops_oldest_when_full(self):
        capture = StreamCapture(STREAM_URL)
        capture._frame_queue = queue.Queue(maxsize=2)

        stamp = datetime.now(timezone.utc)

        def frame(n: int) -> Frame:
            return Frame(
                data=make_frame_data(n), frame_number=n, width=4, height=4, timestamp=stamp
            )

        capture._enqueue(frame(1))
        capture._enqueue(frame(2))
        capture._enqueue(frame(3))  # full: drops frame 1

        remaining = [capture._frame_queue.get_nowait().frame_number for _ in range(2)]
        assert remaining == [2, 3]
        assert capture._frame_queue.empty()

    def test_skip_logic_preserved(self):
        """skip=N still yields only every Nth frame by frame_number."""
        fake = FakeVideoCapture([make_frame_data(i) for i in range(4)])
        capture = StreamCapture(STREAM_URL, read_timeout_s=READ_TIMEOUT_S)

        with patch("kanyo.detection.capture.cv2.VideoCapture", return_value=fake):
            gen = capture.frames(skip=2)
            f_a = next(gen)
            f_b = next(gen)
            gen.close()

        assert (f_a.frame_number, f_b.frame_number) == (2, 4)
