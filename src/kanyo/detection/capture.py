"""
Video capture utilities for kanyo detection pipeline.

Handles YouTube stream resolution via yt-dlp and OpenCV video capture
with automatic reconnection support.
"""

from __future__ import annotations

import queue
import random
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator

import cv2
import numpy as np

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)

# Backoff constants for stream reconnection
BACKOFF_MIN_SECONDS = 60
BACKOFF_MAX_SECONDS = 1800  # 30 minutes
BACKOFF_MULTIPLIER = 2.0
BACKOFF_JITTER_FRAC = 0.2
MAX_DAILY_ATTEMPTS = 50  # hard ceiling per stream per 24h

# Reader-thread constants (ho-11 / 023-A)
FRAME_QUEUE_MAXSIZE = 30  # bounded queue between reader thread and consumer
READER_JOIN_TIMEOUT_S = 5.0  # max wait for the reader thread on disconnect


@dataclass
class Frame:
    """A captured video frame with metadata.

    ``timestamp`` is assigned once, in the reader thread, the moment
    ``cap.read()`` returns — it is the single time authority for everything
    derived from this frame (ho-11).
    """

    data: np.ndarray
    frame_number: int
    width: int
    height: int
    timestamp: datetime

    @property
    def shape(self) -> tuple[int, int, int]:
        """Return (height, width, channels)."""
        return self.data.shape


class _ReadFailure:
    """Queue marker: the reader thread saw a failed read (``ret == False``).

    Distinct from the ``None`` no-frame sentinel that ``frames()`` yields on
    a read *timeout* — a read failure triggers the reconnect path, a timeout
    surfaces a silent/blocked stream to the consumer.
    """


_READ_FAILURE = _ReadFailure()


class StreamCapture:
    """
    Captures frames from a video stream with reconnection support.

    Handles YouTube URL resolution via yt-dlp and manages OpenCV
    VideoCapture lifecycle.
    """

    def __init__(
        self,
        stream_url: str,
        max_height: int = 720,
        reconnect_delay: float = 5.0,
        use_tee: bool = False,  # Deprecated, kept for compatibility
        on_connection_issue: Callable[[str], None] | None = None,
        read_timeout_s: float = 10.0,
        now_fn: Callable[[], datetime] | None = None,
    ):
        """
        Initialize stream capture.

        Args:
            stream_url: Video source URL or file path
            max_height: Max resolution for YouTube streams
            reconnect_delay: Seconds to wait before reconnecting
            use_tee: DEPRECATED - ignored, kept for API compatibility
            on_connection_issue: Optional callback for admin notifications on connection issues
            read_timeout_s: Seconds without a frame from the reader thread
                before frames() yields a ``None`` no-frame sentinel
            now_fn: Clock used to stamp frames at read time. Defaults to
                UTC wall clock; the monitor injects the stream's configured
                timezone so frame timestamps match every existing timestamp.
        """
        self.stream_url = stream_url
        self.max_height = max_height
        self.reconnect_delay = reconnect_delay
        self.on_connection_issue = on_connection_issue
        self.read_timeout_s = read_timeout_s
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        self._cap: cv2.VideoCapture | None = None
        self._frame_count = 0
        # Reader thread state (ho-11): the worker reads frames off the
        # stream and pushes them into a bounded queue; frames() consumes.
        self._frame_queue: queue.Queue[Frame | _ReadFailure] = queue.Queue(
            maxsize=FRAME_QUEUE_MAXSIZE
        )
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()
        self._ytdlp_fallback_used = False
        self.ytdlp_opts: dict = {}
        self._last_admin_notification_time: float = 0
        # True only while a "lost" admin alert has been sent and not yet paired
        # with a "reconnected" alert — keeps connectivity alerts in matched pairs.
        self._outage_alert_sent: bool = False
        self._consecutive_failures = 0
        self._attempts_today = 0
        self._attempts_window_start = time.time()

        if use_tee:
            logger.warning("use_tee is deprecated - buffer mode is now default")

    def resolve_youtube_url(self) -> str:
        """
        Resolve YouTube URL to direct stream URL via yt-dlp.

        Returns the direct HLS/DASH URL that OpenCV can read.
        Handles 'Precondition check failed' errors with automatic fallback.
        """
        logger.info(f"Resolving stream URL: {self.stream_url}")

        # Build command with optional extractor args for fallback client
        cmd = [
            "yt-dlp",
            "--js-runtimes",
            "node",
            "-f",
            f"best[height<={self.max_height}]",
            "-g",
            self.stream_url,
        ]

        # Add extractor args if fallback mode is active
        if "extractor_args" in self.ytdlp_opts:
            cmd.extend(["--extractor-args", "youtube:player_client=android_creator"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"yt-dlp failed: {result.stderr}")
            raise RuntimeError(f"Failed to resolve stream URL: {result.stderr}")

        direct_url = result.stdout.strip()
        logger.debug(f"Resolved to: {direct_url[:80]}...")
        return direct_url

    def _compute_backoff(self) -> float:
        """Compute next backoff delay with exponential growth and jitter."""
        base = min(
            BACKOFF_MIN_SECONDS * (BACKOFF_MULTIPLIER**self._consecutive_failures),
            BACKOFF_MAX_SECONDS,
        )
        jitter = base * BACKOFF_JITTER_FRAC * (2 * random.random() - 1)
        return max(BACKOFF_MIN_SECONDS, base + jitter)

    def _check_daily_cap(self) -> bool:
        """Return True if under the daily attempt cap, False if over."""
        now = time.time()
        if now - self._attempts_window_start > 86400:
            self._attempts_today = 0
            self._attempts_window_start = now
        return self._attempts_today < MAX_DAILY_ATTEMPTS

    def connect(self) -> bool:
        """
        Connect to the video stream with exponential backoff on failure.
        Returns True if connection successful, False otherwise.
        """
        if not self._check_daily_cap():
            logger.error(
                f"Daily attempt cap ({MAX_DAILY_ATTEMPTS}) reached for this stream. "
                f"Going dormant until window resets."
            )
            time.sleep(3600)  # sleep an hour, then check again
            return False

        self._attempts_today += 1

        try:
            is_youtube = "youtube.com" in self.stream_url or "youtu.be" in self.stream_url

            if is_youtube:
                direct_url = self.resolve_youtube_url()
            else:
                direct_url = self.stream_url

            self._cap = cv2.VideoCapture(direct_url)

            if not self._cap.isOpened():
                logger.error("Failed to open video stream")
                self._consecutive_failures += 1
                delay = self._compute_backoff()
                logger.warning(
                    f"Backoff: sleeping {delay:.0f}s before next attempt "
                    f"(failure #{self._consecutive_failures})"
                )
                time.sleep(delay)
                return False

            logger.info("✅ Connected to stream")
            # Reset both fallback and backoff state on success
            self._ytdlp_fallback_used = False
            self._consecutive_failures = 0
            return True

        except RuntimeError as e:
            error_message = str(e)

            if "Precondition check failed" in error_message and not self._ytdlp_fallback_used:
                self._ytdlp_fallback_used = True
                logger.warning("YouTube precondition failed; retrying with alternate yt-dlp client")
                self.ytdlp_opts["extractor_args"] = {
                    "youtube": {"player_client": ["android_creator"]}
                }
                return self.connect()

            # All other errors — apply backoff
            self._consecutive_failures += 1
            delay = self._compute_backoff()
            logger.error(f"Connection failed: {e}")
            logger.warning(
                f"Backoff: sleeping {delay:.0f}s before next attempt "
                f"(failure #{self._consecutive_failures})"
            )
            time.sleep(delay)
            return False

        except Exception as e:
            self._consecutive_failures += 1
            delay = self._compute_backoff()
            logger.error(f"Connection failed: {e}")
            logger.warning(
                f"Backoff: sleeping {delay:.0f}s (failure #{self._consecutive_failures})"
            )
            time.sleep(delay)
            return False

    def disconnect(self) -> None:
        """Stop the reader thread and release the video capture.

        Order matters: the stop event is set first, then the capture is
        released (which unblocks a reader stuck in ``cap.read()``), then the
        thread is joined. No thread survives this call in normal operation.
        """
        self._stop_reader.set()

        cap = self._cap
        self._cap = None
        if cap is not None:
            cap.release()
            logger.debug("Disconnected from stream")

        thread = self._reader_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=READER_JOIN_TIMEOUT_S)
            if thread.is_alive():
                # Daemon thread — it cannot block process exit, but a stuck
                # ffmpeg read is worth a loud log (ho-08 precedent).
                logger.warning("Reader thread did not stop within join timeout")
        self._reader_thread = None

    def reconnect(self) -> bool:
        """Disconnect and reconnect. Backoff timing is owned by connect()."""
        logger.warning("Connection lost, reconnecting...")
        self.disconnect()
        return self.connect()

    def read_frame(self) -> Frame | None:
        """
        Read a single frame from the stream.

        Returns None if read fails (stream interrupted). The frame's
        timestamp is assigned here, the moment the read returns — under the
        reader thread this is read time, the single time authority (ho-11).
        """
        if self._cap is None:
            return None

        ret, data = self._cap.read()
        if not ret or data is None:
            return None

        self._frame_count += 1
        h, w = data.shape[:2]
        return Frame(
            data=data,
            frame_number=self._frame_count,
            width=w,
            height=h,
            timestamp=self._now_fn(),
        )

    def _enqueue(self, item: Frame | _ReadFailure) -> None:
        """Push an item onto the bounded frame queue, dropping the oldest when full.

        A stalled consumer must see fresh frames, not stale ones.
        """
        while True:
            try:
                self._frame_queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    pass

    def _reader_loop(self) -> None:
        """Worker thread body: read frames and push them into the queue.

        On read failure a distinct marker is pushed and the loop exits —
        reconnection decisions stay on the consumer side of the queue, and
        the thread is restarted after a successful reconnect.
        """
        while not self._stop_reader.is_set():
            try:
                frame = self.read_frame()
            except Exception as e:
                # A dying capture handle (e.g. released mid-read during
                # disconnect) reads as a failure, never a crash.
                logger.error(f"Reader thread error: {e}")
                frame = None

            if self._stop_reader.is_set():
                break

            if frame is None:
                self._enqueue(_READ_FAILURE)
                break

            self._enqueue(frame)

    def _start_reader(self) -> None:
        """Start the reader thread if it is not already running."""
        if self._reader_thread is not None and self._reader_thread.is_alive():
            return

        # Drain leftovers from a previous connection so the consumer never
        # sees stale frames or failure markers across a reconnect.
        while True:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

        self._stop_reader.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="stream-reader", daemon=True
        )
        self._reader_thread.start()

    def frames(self, skip: int = 0) -> Iterator[Frame | None]:
        """
        Iterate over frames from the stream.

        Args:
            skip: Process every Nth frame (0 = all frames)

        Yields Frame objects, or ``None`` when no frame arrived within
        ``read_timeout_s`` — the explicit no-frame sentinel (ho-11). A
        blocked ``cv2.read()`` is just a quiet queue, and the timeout sees
        it. Handles reconnection automatically; all retry timing is owned by
        connect() — this method does not sleep.
        """
        if not self.connect():
            raise RuntimeError("Failed to connect to stream")
        self._start_reader()

        needs_reconnect = False
        try:
            while True:
                if needs_reconnect:
                    # Send admin alert (throttled to once per hour)
                    if (
                        self.on_connection_issue
                        and (time.time() - self._last_admin_notification_time) > 3600
                    ):
                        self.on_connection_issue(f"Stream connection lost: {self.stream_url}")
                        self._last_admin_notification_time = time.time()
                        self._outage_alert_sent = True

                    if not self.reconnect():
                        # reconnect() slept via connect() backoff — surface
                        # the ongoing outage to the consumer and retry.
                        yield None
                        continue

                    logger.info("✅ Reconnected successfully!")
                    # Only send "reconnected" when a matching "lost" alert was
                    # actually sent (the lost alert is throttled to 1/hour;
                    # unpaired reconnect alerts were pure noise — see 022-D).
                    if self.on_connection_issue and self._outage_alert_sent:
                        self.on_connection_issue("Stream reconnected")
                        self._outage_alert_sent = False

                    self._start_reader()
                    needs_reconnect = False
                    continue

                try:
                    item = self._frame_queue.get(timeout=self.read_timeout_s)
                except queue.Empty:
                    # No frame within the timeout — covers a *blocked* read,
                    # not just a failed one. Explicit no-frame sentinel.
                    yield None
                    continue

                if isinstance(item, _ReadFailure):
                    needs_reconnect = True
                    continue

                # Skip frames if requested
                if skip > 0 and item.frame_number % skip != 0:
                    continue

                yield item

        finally:
            self.disconnect()

    @property
    def frame_count(self) -> int:
        """Total frames read."""
        return self._frame_count

    @property
    def total_frames(self) -> int:
        """Total frames in the video (0 for live streams)."""
        if self._cap is None:
            return 0
        return int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))

    @property
    def fps(self) -> float:
        """Frames per second of the video."""
        if self._cap is None:
            return 0.0
        return self._cap.get(cv2.CAP_PROP_FPS)

    @property
    def is_connected(self) -> bool:
        """True if currently connected."""
        return self._cap is not None and self._cap.isOpened()

    def __enter__(self) -> "StreamCapture":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()
