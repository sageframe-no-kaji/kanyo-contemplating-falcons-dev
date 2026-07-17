"""
Arrival clip recorder for parallel short-duration clip recording.

Manages recording of arrival clips alongside main visit recordings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from kanyo.utils.logger import get_logger
from kanyo.utils.visit_recorder import ffmpeg_log_path

if TYPE_CHECKING:
    from kanyo.detection.buffer_clip_manager import BufferClipManager
    from kanyo.utils.visit_recorder import VisitRecorder

logger = get_logger(__name__)


class ArrivalClipRecorder:
    """
    Manages parallel arrival clip recording.

    Handles the short-duration arrival clip that records in parallel
    with the main visit recording. Automatically stops after reaching
    the configured duration.
    """

    def __init__(self, clip_manager: BufferClipManager):
        """
        Initialize arrival clip recorder.

        Args:
            clip_manager: BufferClipManager instance for creating clips
        """
        self.clip_manager = clip_manager
        self._recorder: VisitRecorder | None = None
        self._clip_path: Path | None = None
        self._frames_written: int = 0
        self._max_frames: int = 0
        self._start_time: datetime | None = None
        self._max_duration_seconds: float = 0.0

    def is_recording(self) -> bool:
        """Check if currently recording an arrival clip."""
        return self._recorder is not None

    def start_recording(
        self,
        arrival_time: datetime,
        lead_in_frames: list,
        frame_size: tuple[int, int],
    ) -> bool:
        """
        Start recording an arrival clip.

        Args:
            arrival_time: When falcon arrived
            lead_in_frames: Buffer frames before arrival
            frame_size: (width, height) of frames

        Returns:
            True if recording started successfully
        """
        if self._recorder is not None:
            # Rapid swap or slow-stream starvation left previous clip open; close it first.
            logger.warning(
                "Arrival clip still active on new arrival — stopping before starting new one"
            )
            self.stop_recording(datetime.now(timezone.utc))

        clip_duration = self.clip_manager.clip_arrival_before + self.clip_manager.clip_arrival_after

        clip_path, recorder = self.clip_manager.create_standalone_arrival_clip(
            arrival_time=arrival_time,
            lead_in_frames=lead_in_frames,
            frame_size=frame_size,
        )

        if recorder:
            self._recorder = recorder
            self._clip_path = clip_path
            self._frames_written = len(lead_in_frames) if lead_in_frames else 0
            self._max_frames = int(clip_duration * self.clip_manager.clip_fps)
            self._start_time = arrival_time
            self._max_duration_seconds = float(clip_duration)
            logger.event(
                f"📹 Arrival clip will record {self._max_frames} frames ({clip_duration}s)"
            )
            return True

        return False

    def write_frame(self, frame_data, current_time: datetime) -> None:
        """
        Write a frame to the arrival clip recording.

        Automatically stops recording when wall-clock duration is reached
        (time-based) or frame count is reached (fallback).

        Args:
            frame_data: Frame to write
            current_time: Current timestamp for stopping recording
        """
        if self._recorder is None:
            return

        self._recorder.write_frame(frame_data)
        self._frames_written += 1

        # Time-based stop: wall-clock elapsed beats frame count when stream runs slow.
        # A slow YouTube stream can starve the frame counter for minutes; wall-clock
        # guarantees the clip closes on schedule regardless of delivery rate.
        if self._start_time is not None:
            elapsed = (current_time - self._start_time).total_seconds()
            if elapsed >= self._max_duration_seconds:
                self.stop_recording(current_time)
                return

        # Frame-count fallback (fast streams, or tz-naive arrival_time edge case)
        if self._frames_written >= self._max_frames:
            self.stop_recording(current_time)

    def stop_recording(self, stop_time: datetime) -> None:
        """
        Stop the arrival clip recording.

        Args:
            stop_time: Timestamp for stopping the recording
        """
        if self._recorder is None:
            return

        clip_path = self._clip_path
        final_path, _ = self._recorder.stop_recording(stop_time)

        # Use the final path from stop_recording (which handles .tmp rename)
        if final_path:
            clip_path = final_path

        # Delete FFmpeg log file after successful recording. The log was
        # created against the .mp4.tmp path; ffmpeg_log_path() resolves the
        # same name whether clip_path is the renamed final .mp4 or the .tmp.
        if clip_path:
            ffmpeg_log = ffmpeg_log_path(clip_path)
            if ffmpeg_log.exists():
                try:
                    ffmpeg_log.unlink()
                except Exception as e:
                    logger.debug(f"Could not delete FFmpeg log: {e}")

        logger.event(
            f"✅ Arrival clip complete: "
            f"{clip_path.name if clip_path else 'unknown'} "
            f"({self._frames_written} frames)"
        )

        # Reset state
        self._recorder = None
        self._clip_path = None
        self._frames_written = 0
        self._max_frames = 0
        self._start_time = None
        self._max_duration_seconds = 0.0

    def rename_to_final(self) -> Path | None:
        """Rename .tmp file to final name. Returns final path."""
        if self._recorder:
            return self._recorder.rename_to_final()
        return None

    def get_temp_path(self) -> Path | None:
        """Return current .tmp file path for deletion."""
        if self._recorder:
            return self._recorder.get_temp_path()
        return None
