"""
Visit recorder for continuous recording during falcon visits.

Records entire visits to video files, allowing clip extraction afterward.
Starts recording when falcon arrives, stops when falcon departs.
"""

from __future__ import annotations

import select
import subprocess
import typing
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from kanyo.utils.encoder import detect_hardware_encoder
from kanyo.utils.logger import get_logger
from kanyo.utils.output import get_output_path

if TYPE_CHECKING:
    from kanyo.utils.frame_buffer import FrameBuffer

logger = get_logger(__name__)


class VisitRecorder:
    """
    Records entire falcon visits to video files.

    When a falcon arrives:
    1. Grab lead-in frames from buffer
    2. Start recording to visit file
    3. Write lead-in frames
    4. Continue writing every frame during visit

    When falcon departs:
    1. Stop recording
    2. File contains entire visit
    3. Clips can be extracted from this file

    Usage:
        recorder = VisitRecorder(clips_dir="clips", fps=30)

        # On arrival
        recorder.start_recording(arrival_time, buffer.get_frames_before(arrival_time, 15))

        # Every frame during visit
        recorder.write_frame(frame)

        # On departure
        visit_file, metadata = recorder.stop_recording(departure_time)
    """

    def __init__(
        self,
        clips_dir: str = "clips",
        fps: int = 30,
        crf: int = 23,
        lead_in_seconds: int = 15,
        lead_out_seconds: int = 15,
    ):
        """
        Initialize visit recorder.

        Args:
            clips_dir: Base directory for visit recordings
            fps: Recording frame rate
            crf: Encoding quality (lower = better)
            lead_in_seconds: Seconds before arrival to include
            lead_out_seconds: Seconds after departure to include
        """
        self.clips_dir = Path(clips_dir)
        self.fps = fps
        self.crf = crf
        self.lead_in_seconds = lead_in_seconds
        self.lead_out_seconds = lead_out_seconds

        # Recording state
        self._process: subprocess.Popen | None = None
        self._visit_path: Path | None = None
        self._visit_start: datetime | None = None
        self._recording_start: datetime | None = None
        self._frame_count: int = 0
        self._events: list[dict] = []
        self._frame_size: tuple[int, int] | None = None
        self._stderr_file: typing.IO | None = None

        # Get encoder
        self._encoder = detect_hardware_encoder()
        logger.info(f"VisitRecorder initialized with encoder: {self._encoder}")

    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._process is not None and self._process.poll() is None

    @property
    def current_visit_path(self) -> Path | None:
        """Path to current visit file being recorded."""
        return self._visit_path

    @property
    def current_offset_seconds(self) -> float:
        """Current offset into the visit recording in seconds."""
        return self._frame_count / self.fps if self.fps > 0 else 0

    def start_recording(
        self,
        arrival_time: datetime,
        lead_in_frames: list = None,
        frame_size: tuple[int, int] = (1280, 720),
    ) -> Path:
        """
        Start recording a new visit.

        Args:
            arrival_time: When the falcon arrived
            lead_in_frames: Pre-arrival frames from buffer (BufferedFrame objects)
            frame_size: Video dimensions (width, height)

        Returns:
            Path to the visit file being created
        """
        if self.is_recording:
            logger.warning("Already recording - stopping previous recording")
            self.stop_recording(datetime.now())

        # Generate output path
        self._visit_path = get_output_path(
            str(self.clips_dir),
            arrival_time,
            "visit",
            "mp4",
        )
        self._visit_path.parent.mkdir(parents=True, exist_ok=True)

        self._visit_start = arrival_time
        self._recording_start = arrival_time - timedelta(seconds=self.lead_in_seconds)
        self._frame_count = 0
        self._events = []
        self._frame_size = frame_size

        # Build ffmpeg command for piped input
        width, height = frame_size
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "bgr24",
            "-r",
            str(self.fps),
            "-i",
            "-",  # Read from stdin
        ]

        # Add encoder-specific options
        if self._encoder == "h264_videotoolbox":
            quality = max(1, min(100, int((51 - self.crf) * 2)))
            cmd.extend([
                "-c:v", "h264_videotoolbox",
                "-q:v", str(quality),
                "-profile:v", "baseline",
                "-level", "3.0",
                "-pix_fmt", "yuv420p"
            ])
        elif self._encoder == "h264_vaapi":
            cmd.extend(
                [
                    "-vaapi_device",
                    "/dev/dri/renderD128",
                    "-vf",
                    "format=nv12,hwupload",
                    "-c:v",
                    "h264_vaapi",
                    "-qp",
                    str(self.crf),
                    "-profile:v", "baseline",
                    "-level", "3.0",
                ]
            )
        elif self._encoder == "h264_nvenc":
            cmd.extend([
                "-c:v", "h264_nvenc",
                "-cq", str(self.crf),
                "-profile:v", "baseline",
                "-level", "3.0",
                "-pix_fmt", "yuv420p"
            ])
        else:
            cmd.extend([
                "-c:v", "libx264",
                "-profile:v", "baseline",
                "-level", "3.0",
                "-pix_fmt", "yuv420p",
                "-crf", str(self.crf),
                "-preset", "fast"
            ])

        cmd.extend(["-movflags", "+faststart", str(self._visit_path)])

        logger.info(f"📹 Starting visit recording: {self._visit_path}")

        try:
            # Write ffmpeg stderr to log file instead of pipe to prevent deadlock.
            # Pipe buffers are finite (~64KB); if ffmpeg writes more than that
            # and we don't read it, ffmpeg blocks, which backs up stdin, which
            # blocks our write_frame() calls forever.
            stderr_log = self._visit_path.with_suffix(".ffmpeg.log")
            self._stderr_file = open(stderr_log, "w")

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
            )
        except Exception as e:
            logger.error(f"Failed to start ffmpeg: {e}")
            if self._stderr_file:
                self._stderr_file.close()
                self._stderr_file = None
            self._process = None
            raise

        # Write lead-in frames from buffer
        if lead_in_frames:
            logger.info(f"Writing {len(lead_in_frames)} lead-in frames")
            for buffered_frame in lead_in_frames:
                frame = buffered_frame.decode()
                self._write_raw_frame(frame)

        # Log arrival event
        self._events.append(
            {
                "type": "arrival",
                "offset_seconds": self.current_offset_seconds,
                "timestamp": arrival_time.isoformat(),
            }
        )

        return self._visit_path

    def write_frame(self, frame: np.ndarray) -> bool:
        """
        Write a frame to the visit recording.

        Args:
            frame: OpenCV frame (BGR numpy array)

        Returns:
            True if written successfully
        """
        if not self.is_recording:
            return False

        # Update frame size if needed
        if self._frame_size is None:
            h, w = frame.shape[:2]
            self._frame_size = (w, h)

        return self._write_raw_frame(frame)

    def _write_raw_frame(self, frame: np.ndarray) -> bool:
        """Write raw frame bytes to ffmpeg stdin with timeout protection."""
        if self._process is None or self._process.stdin is None:
            return False

        try:
            # Check if stdin is ready for writing (timeout 0.5s)
            # This prevents blocking forever if ffmpeg stalls
            stdin_fd = self._process.stdin.fileno()
            _, ready, _ = select.select([], [stdin_fd], [], 0.5)

            if not ready:
                logger.warning("⚠️ FFmpeg stdin not ready - frame dropped (possible encoder stall)")
                return False

            self._process.stdin.write(frame.tobytes())
            self._frame_count += 1
            return True
        except (BrokenPipeError, OSError) as e:
            logger.error(f"Failed to write frame: {e}")
            return False
        except (ValueError, select.error) as e:
            # stdin closed or invalid
            logger.error(f"FFmpeg stdin error: {e}")
            return False

    def log_event(self, event_type: str, timestamp: datetime, metadata: dict = None) -> None:
        """
        Log an event with its offset in the recording.

        Args:
            event_type: Type of event (roosting, etc.)
            timestamp: When the event occurred
            metadata: Additional event data
        """
        event = {
            "type": event_type,
            "offset_seconds": self.current_offset_seconds,
            "timestamp": timestamp.isoformat(),
        }
        if metadata:
            event.update(metadata)
        self._events.append(event)
        logger.debug(f"Logged event: {event_type} at offset {self.current_offset_seconds:.1f}s")

    def stop_recording(self, departure_time: datetime) -> tuple[Path | None, dict]:
        """
        Stop recording and finalize the visit file.

        Args:
            departure_time: When the falcon departed

        Returns:
            Tuple of (path to visit file, metadata dict)
        """
        if not self.is_recording:
            logger.warning("Not currently recording")
            return None, {}

        # Log departure event
        self._events.append(
            {
                "type": "departure",
                "offset_seconds": self.current_offset_seconds,
                "timestamp": departure_time.isoformat(),
            }
        )

        # Close ffmpeg
        try:
            if self._process and self._process.stdin:
                self._process.stdin.close()
            if self._process:
                self._process.wait(timeout=30)

            # Close stderr file
            if self._stderr_file:
                self._stderr_file.close()
                self._stderr_file = None
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg didn't finish in time, killing")
            if self._process:
                self._process.kill()
                self._process.wait()
            # Close stderr file
            if self._stderr_file:
                self._stderr_file.close()
                self._stderr_file = None
        except Exception as e:
            logger.error(f"Error closing ffmpeg: {e}")
            # Close stderr file
            if self._stderr_file:
                self._stderr_file.close()
                self._stderr_file = None

        # Build metadata
        visit_duration = (
            (departure_time - self._visit_start).total_seconds() if self._visit_start else 0
        )
        metadata = {
            "visit_file": str(self._visit_path),
            "visit_start": self._visit_start.isoformat() if self._visit_start else None,
            "visit_end": departure_time.isoformat(),  # This is last_detection time
            "recording_start": (
                self._recording_start.isoformat() if self._recording_start else None
            ),  # When file started (includes lead-in)
            "duration_seconds": visit_duration,
            "recording_duration_seconds": self._frame_count / self.fps,
            "frame_count": self._frame_count,
            "fps": self.fps,
            "events": self._events,
        }

        result_path = self._visit_path

        # Delete FFmpeg log file after successful recording
        if result_path:
            ffmpeg_log = result_path.with_suffix(".ffmpeg.log")
            if ffmpeg_log.exists():
                try:
                    ffmpeg_log.unlink()
                except Exception as e:
                    logger.debug(f"Could not delete FFmpeg log: {e}")

        # Reset state
        self._process = None
        self._frame_count = 0
        self._events = []

        logger.info(
            f"✅ Visit recording complete: {result_path} "
            f"({visit_duration:.0f}s, {metadata['frame_count']} frames)"
        )

        return result_path, metadata

    def extract_clip(
        self,
        start_offset: float,
        duration: float,
        output_path: Path,
    ) -> bool:
        """
        Extract a clip from the current or a previous visit file.

        Args:
            start_offset: Seconds from start of visit
            duration: Clip duration in seconds
            output_path: Where to save the clip

        Returns:
            True if successful
        """
        if not self._visit_path or not self._visit_path.exists():
            logger.warning("No visit file to extract from")
            return False

        return self.extract_clip_from_file(
            self._visit_path,
            start_offset,
            duration,
            output_path,
        )

    @staticmethod
    def extract_clip_from_file(
        visit_file: Path,
        start_offset: float,
        duration: float,
        output_path: Path,
    ) -> bool:
        """
        Extract a clip from a visit file.

        Args:
            visit_file: Path to visit recording
            start_offset: Seconds from start of recording
            duration: Clip duration in seconds
            output_path: Where to save the clip

        Returns:
            True if successful
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_offset),
            "-i",
            str(visit_file),
            "-t",
            str(duration),
            "-c",
            "copy",  # Fast copy, no re-encoding
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.info(f"Extracting clip: {start_offset:.1f}s + {duration:.1f}s → {output_path}")

        # Write ffmpeg stderr to log file
        ffmpeg_log = output_path.with_suffix(".ffmpeg.log")
        try:
            with open(ffmpeg_log, "w") as stderr_file:
                result = subprocess.run(
                    cmd, stdout=subprocess.DEVNULL, stderr=stderr_file, timeout=60
                )
            if result.returncode != 0:
                logger.error(f"ffmpeg clip extraction failed (see {ffmpeg_log})")
                return False

            # Delete log file after successful extraction
            if ffmpeg_log.exists():
                try:
                    ffmpeg_log.unlink()
                except Exception as e:
                    logger.debug(f"Could not delete FFmpeg log: {e}")

            logger.info(f"✅ Clip extracted: {output_path}")
            return True
        except Exception as e:
            logger.error(f"Clip extraction failed: {e}")
            return False
