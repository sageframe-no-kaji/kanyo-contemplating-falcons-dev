"""
Arrival clip recorder for parallel short-duration clip recording.

Manages recording of arrival clips alongside main visit recordings.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from kanyo.utils.logger import get_logger

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
        clip_duration = self.clip_manager.clip_arrival_before + self.clip_manager.clip_arrival_after

        clip_path, recorder = self.clip_manager.create_standalone_arrival_clip(
            arrival_time=arrival_time,
            lead_in_frames=lead_in_frames,
            frame_size=frame_size,
        )

        if recorder:
            self._recorder = recorder
            self._clip_path = clip_path
            self._frames_written = 0
            self._max_frames = int(clip_duration * self.clip_manager.clip_fps)
            logger.event(
                f"📹 Arrival clip will record {self._max_frames} frames ({clip_duration}s)"
            )
            return True

        return False

    def write_frame(self, frame_data, current_time: datetime) -> None:
        """
        Write a frame to the arrival clip recording.

        Automatically stops recording when max frames reached.

        Args:
            frame_data: Frame to write
            current_time: Current timestamp for stopping recording
        """
        if self._recorder is None:
            return

        self._recorder.write_frame(frame_data)
        self._frames_written += 1

        # Stop recording after max frames reached
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

        # Delete FFmpeg log file after successful recording
        if clip_path:
            ffmpeg_log = clip_path.with_suffix(".ffmpeg.log")
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
