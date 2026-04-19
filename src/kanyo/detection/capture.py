"""
Video capture utilities for kanyo detection pipeline.

Handles YouTube stream resolution via yt-dlp and OpenCV video capture
with automatic reconnection support.
"""

from __future__ import annotations

import random
import subprocess
import time
from dataclasses import dataclass
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


@dataclass
class Frame:
    """A captured video frame with metadata."""

    data: np.ndarray
    frame_number: int
    width: int
    height: int

    @property
    def shape(self) -> tuple[int, int, int]:
        """Return (height, width, channels)."""
        return self.data.shape


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
    ):
        """
        Initialize stream capture.

        Args:
            stream_url: Video source URL or file path
            max_height: Max resolution for YouTube streams
            reconnect_delay: Seconds to wait before reconnecting
            use_tee: DEPRECATED - ignored, kept for API compatibility
            on_connection_issue: Optional callback for admin notifications on connection issues
        """
        self.stream_url = stream_url
        self.max_height = max_height
        self.reconnect_delay = reconnect_delay
        self.on_connection_issue = on_connection_issue
        self._cap: cv2.VideoCapture | None = None
        self._frame_count = 0
        self._ytdlp_fallback_used = False
        self.ytdlp_opts: dict = {}
        self._last_admin_notification_time: float = 0
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
        """Release the video capture."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.debug("Disconnected from stream")

    def reconnect(self) -> bool:
        """Disconnect and reconnect. Backoff timing is owned by connect()."""
        logger.warning("Connection lost, reconnecting...")
        self.disconnect()
        return self.connect()

    def read_frame(self) -> Frame | None:
        """
        Read a single frame from the stream.

        Returns None if read fails (stream interrupted).
        """
        if self._cap is None:
            return None

        ret, data = self._cap.read()
        if not ret or data is None:
            return None

        self._frame_count += 1
        h, w = data.shape[:2]
        return Frame(data=data, frame_number=self._frame_count, width=w, height=h)

    def frames(self, skip: int = 0) -> Iterator[Frame]:
        """
        Iterate over frames from the stream.

        Args:
            skip: Process every Nth frame (0 = all frames)

        Yields Frame objects. Handles reconnection automatically.
        All retry timing is owned by connect() — this method does not sleep.
        """
        if not self.connect():
            raise RuntimeError("Failed to connect to stream")

        try:
            while True:
                frame = self.read_frame()

                if frame is None:
                    # Send admin alert (throttled to once per hour)
                    if (
                        self.on_connection_issue
                        and (time.time() - self._last_admin_notification_time) > 3600
                    ):
                        self.on_connection_issue(f"Stream connection lost: {self.stream_url}")
                        self._last_admin_notification_time = time.time()

                    if not self.reconnect():
                        continue  # reconnect() sleeps via connect() backoff

                    logger.info("✅ Reconnected successfully!")
                    if self.on_connection_issue:
                        self.on_connection_issue("Stream reconnected")
                    continue

                # Skip frames if requested
                if skip > 0 and frame.frame_number % skip != 0:
                    continue

                yield frame

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
