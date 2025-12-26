"""
Clip manager for creating video clips from falcon events.

Handles extraction of arrival, departure, visit, state change, and final clips.
"""

from concurrent.futures import ThreadPoolExecutor
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
    at appropriate times (arrival, departure, state changes, shutdown).

    Clip Strategy:
    - ARRIVAL: 15s before, 30s after (capture what happens after landing)
    - DEPARTURE: 30s before, 15s after (capture what triggered exit)
    - SHORT VISITS: If visit < short_visit_threshold, save as one clip
    - STATE CHANGES: 15s before, 30s after, with cooldown to prevent spam
    """

    def __init__(
        self,
        capture,
        clips_dir: str = "clips",
        clip_fps: int = 30,
        clip_crf: int = 23,
        # Arrival clip timing
        clip_arrival_before: int = 15,
        clip_arrival_after: int = 30,
        # Departure clip timing
        clip_departure_before: int = 30,
        clip_departure_after: int = 15,
        # State change clip timing
        clip_state_change_before: int = 15,
        clip_state_change_after: int = 30,
        clip_state_change_cooldown: int = 300,
        # Short visit handling
        short_visit_threshold: int = 600,
        # Legacy (deprecated)
        clip_before_seconds: int = 30,
        clip_after_seconds: int = 60,
    ):
        """
        Initialize clip manager.

        Args:
            capture: StreamCapture instance (tee_manager accessed lazily)
            clips_dir: Base directory for saving clips
            clip_fps: Output FPS for clips
            clip_crf: CRF quality setting (lower = better quality)
            clip_arrival_before: Seconds before arrival to include
            clip_arrival_after: Seconds after arrival to include
            clip_departure_before: Seconds before departure to include
            clip_departure_after: Seconds after departure to include
            clip_state_change_before: Seconds before state change
            clip_state_change_after: Seconds after state change
            clip_state_change_cooldown: Min seconds between state change clips
            short_visit_threshold: Visits shorter than this saved as one clip
        """
        self.capture = capture
        self.clips_dir = clips_dir
        self.clip_fps = clip_fps
        self.clip_crf = clip_crf

        # Event-specific timing
        self.clip_arrival_before = clip_arrival_before
        self.clip_arrival_after = clip_arrival_after
        self.clip_departure_before = clip_departure_before
        self.clip_departure_after = clip_departure_after
        self.clip_state_change_before = clip_state_change_before
        self.clip_state_change_after = clip_state_change_after
        self.clip_state_change_cooldown = clip_state_change_cooldown
        self.short_visit_threshold = short_visit_threshold

        # State change debounce tracking
        # Instead of cooldown (create clip immediately, ignore for N seconds),
        # we use debounce (wait for N seconds of quiet, then create clip).
        # This prevents clip spam when falcon is fidgety between states.
        self.pending_state_change: tuple[datetime, str] | None = None  # (event_time, event_name)
        self.state_change_debounce_until: datetime | None = None  # When to create the clip

        # Async clip creation - prevents blocking detection loop during ffmpeg encoding
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clip_")
        self._pending_futures: list = []
        self._shutdown = False

        # Legacy (for backward compatibility)
        self.clip_before_seconds = clip_before_seconds
        self.clip_after_seconds = clip_after_seconds

    def shutdown(self):
        """Shutdown the clip executor, waiting for pending clips to complete."""
        if self._shutdown:
            return  # Already shutdown
        self._shutdown = True
        logger.info("Shutting down clip manager, waiting for pending clips...")
        self._executor.shutdown(wait=True)
        logger.info("Clip manager shutdown complete")

    @property
    def tee_manager(self):
        """Lazily access tee_manager from capture (available after stream connects)."""
        return self.capture.tee_manager if self.capture else None

    def _submit_clip_task(self, func, *args, **kwargs):
        """Submit a clip creation task to run in background."""
        future = self._executor.submit(func, *args, **kwargs)
        self._pending_futures.append(future)
        # Clean up completed futures
        self._pending_futures = [f for f in self._pending_futures if not f.done()]
        return future

    def create_initial_clip(self, detection_time: datetime) -> bool:
        """
        Create initial state clip when monitoring starts with falcon present.

        This captures what's happening when the monitor starts, which is valuable
        when the monitor restarts and a falcon is already roosting.
        Uses arrival timing (15s before, 30s after detection_time).

        Runs asynchronously - returns immediately after scheduling.

        Args:
            detection_time: When the initial detection was confirmed

        Returns:
            True if clip creation was scheduled, False if it couldn't be started
        """
        if not self.tee_manager:
            logger.warning(
                "Cannot create initial clip: tee_manager not available (not using tee mode?)"
            )
            return False

        clip_center = detection_time
        clip_start = clip_center - timedelta(seconds=self.clip_arrival_before)
        clip_duration = self.clip_arrival_before + self.clip_arrival_after

        clip_path = get_output_path(
            self.clips_dir,
            clip_center,
            "initial",  # Distinct from "arrival" - this is startup state
            "mp4",
        )
        logger.info(
            f"📹 Scheduling initial state clip: {clip_path.name} ({self.clip_arrival_before}s before, {self.clip_arrival_after}s after)"
        )

        self._submit_clip_task(
            self._extract_clip_async,
            clip_start,
            clip_duration,
            clip_path,
            "initial state",
        )
        return True

    def _extract_clip_async(
        self,
        clip_start: datetime,
        clip_duration: float,
        clip_path: Path,
        clip_type: str,
    ) -> str | None:
        """
        Internal method to extract clip - runs in background thread.

        Args:
            clip_start: When the clip should start
            clip_duration: Duration in seconds
            clip_path: Where to save the clip
            clip_type: Description for logging (e.g., "initial state", "arrival")

        Returns:
            Path to created clip, or None if creation failed
        """
        try:
            success = self.tee_manager.extract_clip(
                start_time=clip_start,
                duration_seconds=clip_duration,
                output_path=clip_path,
                fps=self.clip_fps,
                crf=self.clip_crf,
            )

            if success:
                logger.info(f"✅ {clip_type.capitalize()} clip saved: {clip_path}")
                return str(clip_path)
            else:
                logger.warning(f"Failed to create {clip_type} clip")
                return None

        except Exception as e:
            logger.error(f"Error creating {clip_type} clip: {e}")
            return None

    def create_arrival_clip(self, arrival_time: datetime) -> bool:
        """
        Create arrival clip for a falcon visit.
        Uses configured timing: clip_arrival_before, clip_arrival_after

        Runs asynchronously - returns immediately after scheduling.

        Args:
            arrival_time: When the falcon arrived

        Returns:
            True if clip creation was scheduled, False if it couldn't be started
        """
        if not self.tee_manager:
            logger.warning(
                "Cannot create arrival clip: tee_manager not available (not using tee mode?)"
            )
            return False

        clip_center = arrival_time
        clip_start = clip_center - timedelta(seconds=self.clip_arrival_before)
        clip_duration = self.clip_arrival_before + self.clip_arrival_after

        clip_path = get_output_path(
            self.clips_dir,
            clip_center,
            "arrival",
            "mp4",
        )
        logger.info(
            f"📹 Scheduling arrival clip: {clip_path.name} ({self.clip_arrival_before}s before, {self.clip_arrival_after}s after)"
        )

        self._submit_clip_task(
            self._extract_clip_async,
            clip_start,
            clip_duration,
            clip_path,
            "arrival",
        )
        return True

    def create_visit_clip(self, start_time: datetime, end_time: datetime) -> bool:
        """
        Create a clip for the entire visit (for short visits < 10 minutes).

        Runs asynchronously - returns immediately after scheduling.

        Args:
            start_time: Visit start timestamp
            end_time: Visit end timestamp

        Returns:
            True if clip creation was scheduled, False if it couldn't be started
        """
        if not self.tee_manager:
            logger.warning(
                "Cannot create visit clip: tee_manager not available (not using tee mode?)"
            )
            return False

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
        logger.info(f"📹 Scheduling full visit clip: {clip_path.name} ({clip_duration:.0f}s total)")

        self._submit_clip_task(
            self._extract_clip_async,
            clip_start,
            clip_duration,
            clip_path,
            "visit",
        )
        return True

    def create_departure_clip(self, departure_time: datetime) -> bool:
        """
        Create departure clip for a falcon visit.
        Uses configured timing: clip_departure_before, clip_departure_after

        Runs asynchronously - returns immediately after scheduling.

        Args:
            departure_time: When the falcon departed

        Returns:
            True if clip creation was scheduled, False if it couldn't be started
        """
        if not self.tee_manager:
            logger.warning(
                "Cannot create departure clip: tee_manager not available (not using tee mode?)"
            )
            return False

        clip_center = departure_time
        clip_start = clip_center - timedelta(seconds=self.clip_departure_before)
        clip_duration = self.clip_departure_before + self.clip_departure_after

        clip_path = get_output_path(
            self.clips_dir,
            clip_center,
            "departure",
            "mp4",
        )
        logger.info(
            f"📹 Scheduling departure clip: {clip_path.name} ({self.clip_departure_before}s before, {self.clip_departure_after}s after)"
        )

        self._submit_clip_task(
            self._extract_clip_async,
            clip_start,
            clip_duration,
            clip_path,
            "departure",
        )
        return True

    def schedule_state_change_clip(self, event_time: datetime, event_name: str) -> None:
        """
        Schedule a state change clip using debounce pattern.

        Instead of creating immediately with cooldown, we schedule the clip
        for clip_state_change_cooldown seconds in the future. If another
        state change happens before then, the timer resets (rolling debounce).

        This prevents clip spam when falcon is fidgety between ROOSTING/ACTIVITY.
        The clip is only created after activity has "settled" for the cooldown period.

        Args:
            event_time: When the event occurred
            event_name: Name of the event (for filename)
        """
        debounce_until = event_time + timedelta(seconds=self.clip_state_change_cooldown)

        if self.pending_state_change:
            old_name = self.pending_state_change[1]
            logger.info(
                f"🔄 State change debounce reset: {old_name} → {event_name} (will create at {debounce_until.strftime('%H:%M:%S')})"
            )
        else:
            logger.info(
                f"⏳ State change scheduled: {event_name} clip in {self.clip_state_change_cooldown}s (debounce until {debounce_until.strftime('%H:%M:%S')})"
            )

        self.pending_state_change = (event_time, event_name)
        self.state_change_debounce_until = debounce_until

    def check_state_change_debounce(self, current_time: datetime) -> bool:
        """
        Check if debounce period has expired and schedule the pending clip.

        Should be called periodically by the monitor (e.g., in process_frame).

        Args:
            current_time: Current timestamp

        Returns:
            True if clip was scheduled, False otherwise
        """
        if not self.pending_state_change or not self.state_change_debounce_until:
            return False

        if current_time < self.state_change_debounce_until:
            return False  # Still debouncing

        # Debounce expired - create the clip
        event_time, event_name = self.pending_state_change
        logger.info(f"✅ State change debounce complete - scheduling {event_name} clip")

        # Clear pending state before creating (in case of error)
        self.pending_state_change = None
        self.state_change_debounce_until = None

        self._create_state_change_clip_internal(event_time, event_name)
        return True  # Clip was scheduled

    def _create_state_change_clip_internal(self, event_time: datetime, event_name: str) -> bool:
        """
        Internal method to schedule the state change clip (async).
        Uses configured timing: clip_state_change_before, clip_state_change_after

        Args:
            event_time: When the event occurred
            event_name: Name of the event (for filename)

        Returns:
            True if clip creation was scheduled, False if it couldn't be started
        """
        if not self.tee_manager:
            logger.warning(
                f"Cannot create {event_name} clip: tee_manager not available (not using tee mode?)"
            )
            return False

        clip_center = event_time
        clip_start = clip_center - timedelta(seconds=self.clip_state_change_before)
        clip_duration = self.clip_state_change_before + self.clip_state_change_after

        clip_path = get_output_path(
            self.clips_dir,
            clip_center,
            event_name.lower(),
            "mp4",
        )
        logger.info(
            f"📹 Scheduling {event_name} clip: {clip_path.name} ({self.clip_state_change_before}s before, {self.clip_state_change_after}s after)"
        )

        self._submit_clip_task(
            self._extract_clip_async,
            clip_start,
            clip_duration,
            clip_path,
            event_name,
        )
        return True

    def cancel_pending_state_change(self) -> None:
        """Cancel any pending state change clip (e.g., on departure)."""
        if self.pending_state_change:
            event_name = self.pending_state_change[1]
            logger.info(f"🚫 Cancelled pending {event_name} clip (falcon departed)")
            self.pending_state_change = None
            self.state_change_debounce_until = None

    def create_final_clip(self, timestamp: datetime, last_frame=None) -> str | None:
        """
        Create clip when monitor stops with falcon still present.

        NOTE: This runs synchronously (blocking) because at shutdown we want
        to ensure the clip is created before the process exits.

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
