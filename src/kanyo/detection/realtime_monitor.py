"""
Real-time falcon detection with notifications.

Monitors live stream continuously, sends alerts when falcons appear.
Uses StreamCapture for video, FalconDetector for inference,
and EventStore for persistence.
"""
import os
os.environ['OPENCV_FFMPEG_LOGLEVEL'] = '-8'
import time
from datetime import datetime
from pathlib import Path

import cv2

from kanyo.detection.capture import StreamCapture
from kanyo.detection.detect import FalconDetector
from kanyo.detection.events import EventStore, FalconVisit
from kanyo.utils.config import load_config
from kanyo.utils.logger import get_logger, setup_logging_from_config
from kanyo.utils.notifications import send_email

logger = get_logger(__name__)

# Default stream
DEFAULT_STREAM_URL = "https://www.youtube.com/watch?v=glczTFRRAK4"


class RealtimeMonitor:
    """
    Monitors a live stream for falcon activity.

    Orchestrates:
    - StreamCapture: video frame capture with reconnection
    - FalconDetector: YOLO-based animal detection
    - EventStore: JSON persistence for visit events
    """

    def __init__(
        self,
        stream_url: str = DEFAULT_STREAM_URL,
        confidence_threshold: float = 0.5,
        exit_timeout_seconds: int = 120,
        process_interval_frames: int = 30,
        detect_any_animal: bool = True,
        animal_classes: list[int] | None = None,
        use_tee: bool = False,
        proxy_url: str | None = None,
        buffer_dir: str | None = None,
        chunk_minutes: int = 10,
        output_fps: int = 30,
        clip_before_seconds: int = 30,
        clip_after_seconds: int = 60,
        clip_fps: int = 30,
        clip_crf: int = 23,
        clips_dir: str = "clips",
    ):
        self.stream_url = stream_url
        self.exit_timeout = exit_timeout_seconds
        self.process_interval = process_interval_frames
        self.clip_before_seconds = clip_before_seconds
        self.clip_after_seconds = clip_after_seconds
        self.clip_fps = clip_fps
        self.clip_crf = clip_crf
        self.clips_dir = clips_dir

        # Components (orchestrated modules)
        self.capture = StreamCapture(
            stream_url,
            use_tee=use_tee,
            proxy_url=proxy_url,
            buffer_dir=buffer_dir,
            chunk_minutes=chunk_minutes,
            output_fps=output_fps,
        )
        self.detector = FalconDetector(
            confidence_threshold=confidence_threshold,
            detect_any_animal=detect_any_animal,
            animal_classes=animal_classes,
        )
        self.event_store = EventStore()

        # State
        self.current_visit: FalconVisit | None = None
        self.last_detection_time: datetime | None = None
        self.arrival_clip_scheduled: datetime | None = None  # Time when arrival clip should be created
        self.last_frame = None  # Store last frame for exit/final thumbnail

    def get_output_path(self, timestamp: datetime, event_type: str, extension: str) -> Path:
        """
        Generate date-organized output path for clips/thumbnails.

        Args:
            timestamp: Event timestamp
            event_type: 'arrival', 'departure', or 'final'
            extension: 'mp4' or 'jpg'

        Returns:
            Path like: clips/2023-12-17/falcon_143025_arrival.mp4
        """
        date_dir = Path(self.clips_dir) / timestamp.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        filename = f"falcon_{timestamp.strftime('%H%M%S')}_{event_type}.{extension}"
        return date_dir / filename

    def save_thumbnail(self, frame_data, timestamp: datetime, event_type: str) -> str:
        """Save frame as timestamped thumbnail."""
        path = self.get_output_path(timestamp, event_type, "jpg")
        cv2.imwrite(str(path), frame_data)
        logger.debug(f"Saved thumbnail: {path}")
        return str(path)

    def send_notification(self, subject: str, message: str) -> None:
        """Send notification (email/SMS)."""
        try:
            send_email(to="notifications@example.com", subject=subject, body=message)
            logger.info(f"Notification sent: {subject}")
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def process_frame(self, frame) -> None:
        """Process a single frame for falcon detection."""
        now = datetime.now()

        # Run detection (detect_birds filters by target_classes which includes animals)
        detections = self.detector.detect_birds(frame, timestamp=now)
        falcon_present = len(detections) > 0

        if falcon_present:
            best = self.detector.get_best_detection(detections)
            confidence = best.confidence if best else 0

            if self.current_visit is None:
                # FALCON ENTERED
                self.current_visit = FalconVisit(
                    start_time=now,
                    peak_confidence=confidence,
                )
                self.current_visit.thumbnail_path = self.save_thumbnail(frame, now, "arrival")

                logger.info(f"🦅 FALCON ENTERED at {now.strftime('%I:%M:%S %p')}")
                self.send_notification(
                    subject="🦅 Falcon Active Now!",
                    message=(
                        f"Falcon detected at {now.strftime('%I:%M %p')}.\n\n"
                        f"Watch: {self.stream_url}"
                    ),
                )

                # Schedule arrival clip creation after clip_after_seconds
                if self.capture.tee_manager:
                    from datetime import timedelta

                    self.arrival_clip_scheduled = now + timedelta(seconds=self.clip_after_seconds)
                    logger.info(
                        f"Arrival clip scheduled for {self.arrival_clip_scheduled.strftime('%I:%M:%S %p')} "
                        f"({self.clip_after_seconds}s from now)"
                    )
            else:
                # Still present - update peak confidence
                if confidence > self.current_visit.peak_confidence:
                    self.current_visit.peak_confidence = confidence

            self.last_detection_time = now
            self.last_frame = frame  # Store for potential exit thumbnail

            # Check if it's time to create scheduled arrival clip
            if (
                self.arrival_clip_scheduled
                and now >= self.arrival_clip_scheduled
                and self.current_visit
                and not self.current_visit.arrival_clip_path
            ):
                self._create_arrival_clip()
                self.arrival_clip_scheduled = None

        elif self.current_visit is not None:
            # No detection - check if falcon left
            if self.last_detection_time:
                elapsed = (now - self.last_detection_time).total_seconds()
                if elapsed > self.exit_timeout:
                    # FALCON EXITED
                    # Set end_time to when falcon actually left (exit_timeout seconds ago)
                    from datetime import timedelta

                    exit_time = now - timedelta(seconds=self.exit_timeout)
                    self.current_visit.end_time = exit_time
                    logger.info(f"🦅 FALCON EXITED after {self.current_visit.duration_str}")

                    # Save exit thumbnail (using last frame with falcon present)
                    if self.last_frame is not None:
                        exit_thumb_path = self.save_thumbnail(self.last_frame, exit_time, "departure")
                        logger.debug(f"Saved exit thumbnail: {exit_thumb_path}")

                    # Create departure clip (we've already waited exit_timeout, which is > clip_after_seconds)
                    if self.capture.tee_manager:
                        try:
                            from pathlib import Path

                            # Create clip centered on exit time
                            clip_start = exit_time - timedelta(seconds=self.clip_before_seconds)
                            clip_duration = self.clip_before_seconds + self.clip_after_seconds

                            clip_path = self.get_output_path(exit_time, "departure", "mp4")
                            logger.info(f"Creating departure clip: {clip_path.name}")

                            success = self.capture.tee_manager.extract_clip(
                                start_time=clip_start,
                                duration_seconds=clip_duration,
                                output_path=clip_path,
                                fps=self.clip_fps,
                                crf=self.clip_crf,
                            )

                            if success:
                                logger.info(f"✅ Departure clip saved: {clip_path}")
                                self.current_visit.departure_clip_path = str(clip_path)
                            else:
                                logger.warning("Failed to create departure clip")

                        except Exception as e:
                            logger.error(f"Error creating departure clip: {e}")

                    # Persist and reset
                    self.event_store.append(self.current_visit)
                    self.current_visit = None
                    self.last_detection_time = None
                    self.arrival_clip_scheduled = None
                    self.last_frame = None

    def _create_arrival_clip(self) -> None:
        """Create arrival clip for current visit."""
        if not self.current_visit or not self.capture.tee_manager:
            return

        try:
            from pathlib import Path
            from datetime import timedelta

            # Use the visit start time as the clip center point
            clip_center = self.current_visit.start_time
            clip_start = clip_center - timedelta(seconds=self.clip_before_seconds)
            clip_duration = self.clip_before_seconds + self.clip_after_seconds

            clip_path = self.get_output_path(clip_center, "arrival", "mp4")
            logger.info(f"Creating arrival clip: {clip_path.name}")

            success = self.capture.tee_manager.extract_clip(
                start_time=clip_start,
                duration_seconds=clip_duration,
                output_path=clip_path,
                fps=self.clip_fps,
                crf=self.clip_crf,
            )

            if success:
                logger.info(f"✅ Arrival clip saved: {clip_path}")
                self.current_visit.arrival_clip_path = str(clip_path)
            else:
                logger.warning("Failed to create arrival clip")

        except Exception as e:
            logger.error(f"Error creating arrival clip: {e}")

    def create_final_clip(self) -> None:
        """Create clip of current visit if falcon still present when monitor stops."""
        if not self.current_visit:
            return

        now = datetime.now()

        # Save final thumbnail if we have a frame
        if self.last_frame is not None:
            final_thumb_path = self.save_thumbnail(self.last_frame, now, "final")
            logger.debug(f"Saved final thumbnail: {final_thumb_path}")

        if not self.capture.tee_manager:
            return

        try:
            from datetime import timedelta

            # Create clip of last N seconds before shutdown
            clip_duration = self.clip_before_seconds + self.clip_after_seconds
            clip_start = now - timedelta(seconds=clip_duration)

            clip_path = self.get_output_path(now, "final", "mp4")
            logger.info(
                f"Monitor ending with falcon present - creating final clip: {clip_path.name}"
            )

            success = self.capture.tee_manager.extract_clip(
                start_time=clip_start,
                duration_seconds=clip_duration,
                output_path=clip_path,
                fps=self.clip_fps,
                crf=self.clip_crf,
            )

            if success:
                logger.info(f"✅ Final clip saved: {clip_path}")
            else:
                logger.warning("Failed to create final clip")

        except Exception as e:
            logger.error(f"Error creating final clip: {e}")

    def run(self) -> None:
        """Main monitoring loop using StreamCapture."""
        logger.info("=" * 60)
        logger.info("Starting Real-Time Falcon Monitoring")
        logger.info(f"Stream: {self.stream_url}")
        logger.info(f"Exit timeout: {self.exit_timeout}s")
        logger.info(f"Process interval: every {self.process_interval} frames")
        logger.info("=" * 60)

        logger.info("Press Ctrl+C to stop")

        try:
            # Use StreamCapture's frame iterator (handles reconnection)
            for frame in self.capture.frames(skip=self.process_interval):
                self.process_frame(frame.data)
                time.sleep(0.01)  # Prevent CPU spin

        except KeyboardInterrupt:
            logger.info("\nStopping monitoring...")

        finally:
            # Create final clip if falcon still present
            self.create_final_clip()

            self.capture.disconnect()
            # Save any ongoing visit
            if self.current_visit is not None:
                self.current_visit.end_time = datetime.now()
                self.event_store.append(self.current_visit)
                logger.info(f"Saved ongoing visit: {self.current_visit.duration_str}")
            logger.info("Monitoring stopped")


def main():
    """Entry point for real-time monitoring."""
    config = load_config()
    setup_logging_from_config(config)
    logger = get_logger(__name__)

    logger.info("Configuration loaded:")
    logger.info(f"  video_source: {config.get('video_source')}")
    logger.info(f"  detection_confidence: {config.get('detection_confidence')}")
    logger.info(f"  frame_interval: {config.get('frame_interval', 30)}")
    logger.info(f"  detect_any_animal: {config.get('detect_any_animal', True)}")
    logger.info(f"  animal_classes: {config.get('animal_classes')}")
    logger.info(f"  exit_timeout: {config.get('exit_timeout', 120)}")
    logger.info(f"  live_use_ffmpeg_tee: {config.get('live_use_ffmpeg_tee', False)}")

    try:
        monitor = RealtimeMonitor(
            stream_url=config.get("video_source", DEFAULT_STREAM_URL),
            confidence_threshold=config.get("detection_confidence", 0.5),
            exit_timeout_seconds=config.get("exit_timeout", 120),
            process_interval_frames=config.get("frame_interval", 30),
            detect_any_animal=config.get("detect_any_animal", True),
            animal_classes=config.get("animal_classes"),
            use_tee=config.get("live_use_ffmpeg_tee", False),
            proxy_url=config.get("live_proxy_url"),
            buffer_dir=config.get("buffer_dir"),
            chunk_minutes=config.get("continuous_chunk_minutes", 10),
            output_fps=config.get("clip_fps", 30),
            clip_before_seconds=config.get("clip_entrance_before", 30),
            clip_after_seconds=config.get("clip_entrance_after", 60),
            clip_fps=config.get("clip_fps", 30),
            clip_crf=config.get("clip_crf", 23),
            clips_dir=config.get("clips_dir", "clips"),
        )
        monitor.run()
    except Exception as e:
        logger.error(f"Monitor failed: {e}")
        raise


if __name__ == "__main__":
    main()
