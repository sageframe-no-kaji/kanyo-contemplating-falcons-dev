"""
Clip manager for creating video clips from falcon events.

Handles extraction of arrival, departure, visit, and final clips.
"""

from datetime import datetime, timedelta
from pathlib import Path

import cv2

from kanyo.utils.logger import get_logger
from kanyo.utils.output import get_output_path, save_thumbnail

logger = get_logger(__name__)


class ClipManager:
    """
    Manages video clip creation for falcon events.

    Coordinates with StreamCapture's TeeManager to extract clips
    at appropriate times (arrival, departure, visit end, shutdown).
    """

    def __init__(
        self,
        tee_manager,
        clips_dir: str = "clips",
        clip_before_seconds: int = 30,
        clip_after_seconds: int = 60,
        clip_fps: int = 30,
        clip_crf: int = 23,
    ):
        """
        Initialize clip manager.

        Args:
            tee_manager: StreamCapture's TeeManager for clip extraction
            clips_dir: Base directory for saving clips
            clip_before_seconds: Seconds of video before event
            clip_after_seconds: Seconds of video after event
            clip_fps: Output FPS for clips
            clip_crf: CRF quality setting (lower = better quality)
        """
        self.tee_manager = tee_manager
        self.clips_dir = clips_dir
        self.clip_before_seconds = clip_before_seconds
        self.clip_after_seconds = clip_after_seconds
        self.clip_fps = clip_fps
        self.clip_crf = clip_crf

    def create_arrival_clip(self, arrival_time: datetime) -> str | None:
        """
        Create arrival clip for a falcon visit.

        Args:
            arrival_time: When the falcon arrived

        Returns:
            Path to created clip, or None if creation failed
        """
        if not self.tee_manager:
            return None

        try:
            clip_center = arrival_time
            clip_start = clip_center - timedelta(seconds=self.clip_before_seconds)
            clip_duration = self.clip_before_seconds + self.clip_after_seconds

            clip_path = get_output_path(
                self.clips_dir,
                clip_center,
                "arrival",
                "mp4",
            )
            logger.info(f"Creating arrival clip: {clip_path.name}")

            success = self.tee_manager.extract_clip(
                start_time=clip_start,
                duration_seconds=clip_duration,
                output_path=clip_path,
                fps=self.clip_fps,
                crf=self.clip_crf,
            )

            if success:
                logger.info(f"✅ Arrival clip saved: {clip_path}")
                return str(clip_path)
            else:
                logger.warning("Failed to create arrival clip")
                return None

        except Exception as e:
            logger.error(f"Error creating arrival clip: {e}")
            return None

    def create_visit_clip(self, start_time: datetime, end_time: datetime) -> str | None:
        """
        Create a clip for the entire visit.

        Args:
            start_time: Visit start timestamp
            end_time: Visit end timestamp

        Returns:
            Path to created clip, or None if creation failed
        """
        if not self.tee_manager:
            logger.warning("Cannot create visit clip: tee_manager not available (not using tee mode?)")
            return None

        try:
            # Add buffer before and after
            clip_start = start_time - timedelta(seconds=self.clip_before_seconds)
            clip_end = end_time + timedelta(seconds=self.clip_after_seconds)
            clip_duration = (clip_end - clip_start).total_seconds()

            clip_path = get_output_path(
                self.clips_dir,
                start_time,
                "visit",
                "mp4",
            )
            logger.info(f"Creating visit clip: {clip_path.name}")

            success = self.tee_manager.extract_clip(
                start_time=clip_start,
                duration_seconds=clip_duration,
                output_path=clip_path,
                fps=self.clip_fps,
                crf=self.clip_crf,
            )

            if success:
                logger.info(f"✅ Visit clip saved: {clip_path}")
                return str(clip_path)
            else:
                logger.warning("Failed to create visit clip")
                return None

        except Exception as e:
            logger.error(f"Error creating visit clip: {e}")
            return None

    def create_final_clip(self, timestamp: datetime, last_frame=None) -> str | None:
        """
        Create clip when monitor stops with falcon still present.

        Args:
            timestamp: Time when monitoring stopped
            last_frame: Optional last frame for thumbnail

        Returns:
            Path to created clip, or None if creation failed
        """
        # Save final thumbnail if we have a frame
        if last_frame is not None:
            final_thumb_path = save_thumbnail(
                last_frame,
                self.clips_dir,
                timestamp,
                "final",
            )
            logger.debug(f"Saved final thumbnail: {final_thumb_path}")

        if not self.tee_manager:
            return None

        try:
            # Create clip of last N seconds before shutdown
            clip_duration = self.clip_before_seconds + self.clip_after_seconds
            clip_start = timestamp - timedelta(seconds=clip_duration)

            clip_path = get_output_path(
                self.clips_dir,
                timestamp,
                "final",
                "mp4",
            )
            logger.info(
                f"Monitor ending with falcon present - creating final clip: {clip_path.name}"
            )

            success = self.tee_manager.extract_clip(
                start_time=clip_start,
                duration_seconds=clip_duration,
                output_path=clip_path,
                fps=self.clip_fps,
                crf=self.clip_crf,
            )

            if success:
                logger.info(f"✅ Final clip saved: {clip_path}")
                return str(clip_path)
            else:
                logger.warning("Failed to create final clip")
                return None

        except Exception as e:
            logger.error(f"Error creating final clip: {e}")
            return None
