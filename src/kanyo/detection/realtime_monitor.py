"""
Real-time falcon detection with notifications.

Monitors live stream continuously, sends alerts when falcons appear.
Uses StreamCapture for video, FalconDetector for inference,
and EventStore for persistence.
"""

import cv2
import time
from datetime import datetime
from pathlib import Path

from kanyo.detection.capture import StreamCapture
from kanyo.detection.detect import FalconDetector
from kanyo.detection.events import FalconVisit, EventStore
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
    ):
        self.stream_url = stream_url
        self.exit_timeout = exit_timeout_seconds
        self.process_interval = process_interval_frames

        # Components (orchestrated modules)
        self.capture = StreamCapture(stream_url)
        self.detector = FalconDetector(
            confidence_threshold=confidence_threshold,
            detect_any_animal=detect_any_animal,
            animal_classes=animal_classes,
        )
        self.event_store = EventStore()

        # State
        self.current_visit: FalconVisit | None = None
        self.last_detection_time: datetime | None = None

    def save_thumbnail(self, frame_data, prefix: str = "falcon") -> str:
        """Save frame as timestamped thumbnail."""
        thumbs_dir = Path("data/thumbs")
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = thumbs_dir / f"{prefix}_{timestamp_str}.jpg"
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
                self.current_visit.thumbnail_path = self.save_thumbnail(frame, "enter")

                logger.info(f"🦅 FALCON ENTERED at {now.strftime('%I:%M:%S %p')}")
                self.send_notification(
                    subject="🦅 Falcon Active Now!",
                    message=f"Falcon detected at {now.strftime('%I:%M %p')}.\n\nWatch: {self.stream_url}",
                )
            else:
                # Still present - update peak confidence
                if confidence > self.current_visit.peak_confidence:
                    self.current_visit.peak_confidence = confidence

            self.last_detection_time = now

        elif self.current_visit is not None:
            # No detection - check if falcon left
            if self.last_detection_time:
                elapsed = (now - self.last_detection_time).total_seconds()
                if elapsed > self.exit_timeout:
                    # FALCON EXITED
                    self.current_visit.end_time = now
                    logger.info(
                        f"🦅 FALCON EXITED after {self.current_visit.duration_str}"
                    )

                    # Persist and reset
                    self.event_store.append(self.current_visit)
                    self.current_visit = None
                    self.last_detection_time = None

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

    try:
        monitor = RealtimeMonitor(
            stream_url=config.get("video_source", DEFAULT_STREAM_URL),
            confidence_threshold=config.get("detection_confidence", 0.5),
            exit_timeout_seconds=config.get("exit_timeout", 120),
            process_interval_frames=config.get("frame_interval", 30),
            detect_any_animal=config.get("detect_any_animal", True),
            animal_classes=config.get("animal_classes"),
        )
        monitor.run()
    except Exception as e:
        logger.error(f"Monitor failed: {e}")
        raise


if __name__ == "__main__":
    main()
