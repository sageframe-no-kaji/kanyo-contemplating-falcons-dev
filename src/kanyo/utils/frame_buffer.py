"""
Frame buffer for clip extraction.

Ring buffer of JPEG-compressed frames for memory-efficient buffering.
Used to capture footage before/after events without continuous recording.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from kanyo.utils.encoder import detect_hardware_encoder
from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BufferedFrame:
    """A compressed frame with timestamp metadata."""

    timestamp: datetime
    frame_number: int
    jpeg_data: bytes

    def decode(self) -> np.ndarray:
        """Decode JPEG back to numpy array."""
        arr = np.frombuffer(self.jpeg_data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)


class FrameBuffer:
    """
    Rolling buffer of JPEG-compressed frames for clip extraction.

    Stores frames in memory with automatic eviction of old frames.
    Provides efficient extraction of time ranges for clip creation.

    Memory usage: ~100KB per frame at quality 85
    - 30 fps × 60 seconds = 1800 frames = ~180 MB
    - 30 fps × 120 seconds = 3600 frames = ~360 MB
    """

    def __init__(
        self,
        buffer_seconds: int = 60,
        fps: int = 30,
        jpeg_quality: int = 85,
    ):
        """
        Initialize frame buffer.

        Args:
            buffer_seconds: How many seconds of footage to keep
            fps: Expected frame rate (for sizing the buffer)
            jpeg_quality: JPEG compression quality (1-100, higher = better)
        """
        self.buffer_seconds = buffer_seconds
        self.fps = fps
        self.jpeg_quality = jpeg_quality
        self.max_frames = buffer_seconds * fps

        self._frames: deque[BufferedFrame] = deque(maxlen=self.max_frames)
        self._encode_params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]

        logger.info(
            f"FrameBuffer initialized: {buffer_seconds}s @ {fps}fps = "
            f"{self.max_frames} frames max (~{self.max_frames * 100 // 1024}MB)"
        )

    def add_frame(self, frame: np.ndarray, timestamp: datetime, frame_number: int) -> None:
        """
        Add a frame to the buffer.

        Frame is JPEG-compressed before storage to reduce memory usage.
        Old frames are automatically evicted when buffer is full.
        """
        success, jpeg = cv2.imencode('.jpg', frame, self._encode_params)
        if not success:
            logger.warning(f"Failed to encode frame {frame_number}")
            return

        buffered = BufferedFrame(
            timestamp=timestamp,
            frame_number=frame_number,
            jpeg_data=jpeg.tobytes(),
        )
        self._frames.append(buffered)

    def get_frames_in_range(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[BufferedFrame]:
        """
        Get all frames within a time range.

        Args:
            start_time: Start of range (inclusive)
            end_time: End of range (inclusive)

        Returns:
            List of BufferedFrame objects in chronological order
        """
        return [
            f for f in self._frames
            if start_time <= f.timestamp <= end_time
        ]

    def get_frames_before(
        self,
        timestamp: datetime,
        seconds: float,
    ) -> list[BufferedFrame]:
        """
        Get frames from the specified number of seconds before a timestamp.

        Args:
            timestamp: Reference point
            seconds: How many seconds before to include

        Returns:
            List of BufferedFrame objects in chronological order
        """
        from datetime import timedelta
        start = timestamp - timedelta(seconds=seconds)
        return self.get_frames_in_range(start, timestamp)

    def get_recent_frames(self, seconds: float) -> list[BufferedFrame]:
        """
        Get the most recent N seconds of frames.

        Args:
            seconds: How many seconds of recent footage

        Returns:
            List of BufferedFrame objects in chronological order
        """
        if not self._frames:
            return []

        from datetime import timedelta
        end = self._frames[-1].timestamp
        start = end - timedelta(seconds=seconds)
        return self.get_frames_in_range(start, end)

    def extract_clip(
        self,
        start_time: datetime,
        end_time: datetime,
        output_path: Path,
        fps: int | None = None,
        crf: int = 23,
    ) -> bool:
        """
        Extract frames to a video file.

        Args:
            start_time: Clip start time
            end_time: Clip end time
            output_path: Where to save the clip
            fps: Output frame rate (uses buffer fps if None)
            crf: Encoding quality (lower = better)

        Returns:
            True if successful, False otherwise
        """
        frames = self.get_frames_in_range(start_time, end_time)

        if not frames:
            logger.warning(f"No frames found in range {start_time} to {end_time}")
            return False

        output_fps = fps or self.fps
        return self._write_frames_to_video(frames, output_path, output_fps, crf)

    def _write_frames_to_video(
        self,
        frames: list[BufferedFrame],
        output_path: Path,
        fps: int,
        crf: int,
    ) -> bool:
        """
        Write buffered frames to a video file using ffmpeg pipe.

        Uses hardware encoding when available.
        """
        import subprocess

        if not frames:
            return False

        # Decode first frame to get dimensions
        first_frame = frames[0].decode()
        height, width = first_frame.shape[:2]

        # Get encoder
        encoder = detect_hardware_encoder()

        # Build ffmpeg command for piped input
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{width}x{height}",
            "-pix_fmt", "bgr24",
            "-r", str(fps),
            "-i", "-",  # Read from stdin
        ]

        # Add encoder-specific options
        if encoder == "h264_videotoolbox":
            cmd.extend(["-c:v", "h264_videotoolbox", "-q:v", str(max(1, min(100, int((51 - crf) * 2))))])
        elif encoder == "h264_vaapi":
            cmd.extend([
                "-vaapi_device", "/dev/dri/renderD128",
                "-vf", "format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-qp", str(crf),
            ])
        elif encoder == "h264_nvenc":
            cmd.extend(["-c:v", "h264_nvenc", "-cq", str(crf)])
        else:
            cmd.extend(["-c:v", "libx264", "-crf", str(crf), "-preset", "fast"])

        cmd.extend(["-movflags", "+faststart", str(output_path)])

        logger.info(f"Extracting {len(frames)} frames to {output_path}")

        try:
            # Ensure output directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Write all frames to ffmpeg stdin
            for buffered_frame in frames:
                frame = buffered_frame.decode()
                process.stdin.write(frame.tobytes())

            process.stdin.close()
            _, stderr = process.communicate(timeout=60)

            if process.returncode != 0:
                logger.error(f"ffmpeg failed: {stderr.decode()}")
                return False

            logger.info(f"✅ Clip saved: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to write clip: {e}")
            return False

    def __len__(self) -> int:
        """Number of frames currently in buffer."""
        return len(self._frames)

    @property
    def duration_seconds(self) -> float:
        """Approximate duration of buffered footage in seconds."""
        return len(self._frames) / self.fps if self.fps > 0 else 0

    @property
    def oldest_timestamp(self) -> datetime | None:
        """Timestamp of oldest frame in buffer, or None if empty."""
        return self._frames[0].timestamp if self._frames else None

    @property
    def newest_timestamp(self) -> datetime | None:
        """Timestamp of newest frame in buffer, or None if empty."""
        return self._frames[-1].timestamp if self._frames else None

    def clear(self) -> None:
        """Clear all frames from buffer."""
        self._frames.clear()
