"""
Video capture utilities for kanyo detection pipeline.

Handles YouTube stream resolution via yt-dlp and OpenCV video capture
with automatic reconnection support.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


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
    ):
        self.stream_url = stream_url
        self.max_height = max_height
        self.reconnect_delay = reconnect_delay
        self._cap: cv2.VideoCapture | None = None
        self._frame_count = 0

    def resolve_youtube_url(self) -> str:
        """
        Resolve YouTube URL to direct stream URL via yt-dlp.

        Returns the direct HLS/DASH URL that OpenCV can read.
        """
        logger.info(f"Resolving stream URL: {self.stream_url}")
        result = subprocess.run(
            [
                "yt-dlp",
                "-f",
                f"best[height<={self.max_height}]",
                "-g",
                self.stream_url,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"yt-dlp failed: {result.stderr}")
            raise RuntimeError(f"Failed to resolve stream URL: {result.stderr}")

        direct_url = result.stdout.strip()
        logger.debug(f"Resolved to: {direct_url[:80]}...")
        return direct_url

    def connect(self) -> bool:
        """
        Connect to the video stream.

        Returns True if connection successful.
        """
        try:
            # Check if it's a YouTube URL
            if "youtube.com" in self.stream_url or "youtu.be" in self.stream_url:
                direct_url = self.resolve_youtube_url()
            else:
                direct_url = self.stream_url

            self._cap = cv2.VideoCapture(direct_url)

            if not self._cap.isOpened():
                logger.error("Failed to open video stream")
                return False

            logger.info("✅ Connected to stream")
            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def disconnect(self) -> None:
        """Release the video capture."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.debug("Disconnected from stream")

    def reconnect(self) -> bool:
        """Disconnect and reconnect to the stream."""
        logger.warning(f"Reconnecting in {self.reconnect_delay}s...")
        self.disconnect()
        time.sleep(self.reconnect_delay)
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
        """
        if not self.connect():
            raise RuntimeError("Failed to connect to stream")

        try:
            while True:
                frame = self.read_frame()

                if frame is None:
                    if not self.reconnect():
                        logger.error("Reconnection failed, stopping")
                        break
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
    def is_connected(self) -> bool:
        """True if currently connected."""
        return self._cap is not None and self._cap.isOpened()

    def __enter__(self) -> "StreamCapture":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()
