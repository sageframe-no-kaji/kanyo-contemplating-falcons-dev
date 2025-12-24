"""
Real-time falcon detection with notifications.

Monitors live stream continuously, sends alerts when falcons appear.
Uses StreamCapture for video, FalconDetector for inference,
and EventStore for persistence.
"""

import os

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import cv2  # noqa: E402

from kanyo.detection.capture import StreamCapture  # noqa: E402
from kanyo.detection.detect import FalconDetector  # noqa: E402
from kanyo.detection.events import EventStore, FalconVisit  # noqa: E402
from kanyo.detection.event_types import FalconEvent, FalconState  # noqa: E402
from kanyo.detection.falcon_state import FalconStateMachine  # noqa: E402
from kanyo.utils.config import load_config  # noqa: E402
from kanyo.utils.logger import get_logger, setup_logging_from_config  # noqa: E402
from kanyo.utils.notifications import NotificationManager  # noqa: E402

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
        max_runtime_seconds: int | None = None,
        roosting_threshold: int = 1800,
        roosting_exit_timeout: int = 600,
        activity_timeout: int = 180,
        activity_notification: bool = False,
    ):
        self.stream_url = stream_url
        self.exit_timeout = exit_timeout_seconds
        self.process_interval = process_interval_frames
        self.clip_before_seconds = clip_before_seconds
        self.clip_after_seconds = clip_after_seconds
        self.clip_fps = clip_fps
        self.clip_crf = clip_crf
        self.clips_dir = clips_dir
        self.max_runtime_seconds = max_runtime_seconds

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

        # Set up date-organized events file
        date_str = datetime.now().strftime("%Y-%m-%d")
        date_dir = Path(self.clips_dir) / date_str
        date_dir.mkdir(parents=True, exist_ok=True)
        events_path = date_dir / f"events_{date_str}.json"
        self.event_store = EventStore(events_path)

        # Notifications (will be configured from config in main)
        self.notifications: NotificationManager | None = None

        # State
        self.current_visit: FalconVisit | None = None
        self.last_detection_time: datetime | None = None
        self.arrival_clip_scheduled: datetime | None = (
            None  # Time when arrival clip should be created
        )
        self.last_frame = None  # Store last frame for exit/final thumbnail

        # Store config for event handlers
        self.config = {
            "activity_notification": activity_notification,
        }

        # State machine for intelligent behavior tracking
        self.state_machine = FalconStateMachine({
            "exit_timeout": exit_timeout_seconds,
            "roosting_threshold": roosting_threshold,
            "roosting_exit_timeout": roosting_exit_timeout,
            "activity_timeout": activity_timeout,
        })

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

    def process_frame(self, frame) -> None:
        """Process a single frame for falcon detection."""
        now = datetime.now()

        # Run YOLO detection (detect_birds filters by target_classes which includes animals)
        detections = self.detector.detect_birds(frame, timestamp=now)
        falcon_detected = len(detections) > 0

        # Store frame for thumbnails
        if falcon_detected:
            self.last_frame = frame

        # Update state machine with detection result
        events = self.state_machine.update(falcon_detected, now)

        # Handle any events generated by state machine
        for event_type, event_time, metadata in events:
            self._handle_falcon_event(event_type, event_time, metadata)

    def _create_arrival_clip(self) -> None:
        """Create arrival clip for current visit."""
        if not self.current_visit or not self.capture.tee_manager:
            return

        try:
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

    def _handle_falcon_event(self, event_type: FalconEvent, timestamp: datetime, metadata: dict):
        """
        Handle falcon state machine events.

        Routes events from state machine to appropriate actions:
        notifications, thumbnails, and clip creation.
        """
        if event_type == FalconEvent.ARRIVED:
            logger.info(f"🦅 FALCON ARRIVED at {timestamp.strftime('%I:%M:%S %p')}")
            # Send arrival notification
            if self.notifications:
                # Capture thumbnail if we have a frame
                thumb_path = None
                if self.last_frame is not None:
                    thumb_path = self.save_thumbnail(self.last_frame, timestamp, "arrival")
                self.notifications.send_arrival(timestamp, thumb_path)

        elif event_type == FalconEvent.DEPARTED:
            duration = metadata.get("visit_duration") or metadata.get("total_visit_duration", 0)
            duration_str = self._format_duration(duration)
            activity_count = metadata.get("activity_periods", 0)
            activity_str = f", {activity_count} activity periods" if activity_count > 0 else ""

            logger.info(
                f"🦅 FALCON DEPARTED at {timestamp.strftime('%I:%M:%S %p')} "
                f"({duration_str} visit{activity_str})"
            )

            # Send departure notification
            if self.notifications:
                thumb_path = None
                if self.last_frame is not None:
                    thumb_path = self.save_thumbnail(self.last_frame, timestamp, "departure")
                self.notifications.send_departure(timestamp, thumb_path, duration_str)

            # Create visit clip if we have start and end times
            if "visit_start" in metadata and "visit_end" in metadata:
                self._create_visit_clip(metadata["visit_start"], metadata["visit_end"])

        elif event_type == FalconEvent.ROOSTING:
            duration_str = self._format_duration(metadata.get("visit_duration", 0))
            logger.info(f"🏠 FALCON ROOSTING - settled for long-term stay (visit: {duration_str})")

        elif event_type == FalconEvent.ACTIVITY_START:
            logger.info("🔄 FALCON ACTIVITY - movement during roost")
            # Optional activity notifications (usually disabled)
            if self.notifications and hasattr(self, "config") and self.config.get("activity_notification", False):
                self.notifications.send_arrival(timestamp, None)  # Reuse arrival notification

        elif event_type == FalconEvent.ACTIVITY_END:
            duration_str = self._format_duration(metadata.get("activity_duration", 0))
            logger.info(f"🏠 FALCON SETTLED - returned to roosting after {duration_str} activity")

    def _format_duration(self, seconds: float) -> str:
        """
        Format duration as human-readable string.

        Args:
            seconds: Duration in seconds

        Returns:
            Formatted string like "5m 23s" or "2h 15m"
        """
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s" if secs > 0 else f"{minutes}m"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"

    def _create_visit_clip(self, start_time: datetime, end_time: datetime):
        """
        Create a clip for the entire visit.

        Args:
            start_time: Visit start timestamp
            end_time: Visit end timestamp
        """
        if not self.capture.tee_manager:
            return

        try:
            from datetime import timedelta

            # Add buffer before and after
            clip_start = start_time - timedelta(seconds=self.clip_before_seconds)
            clip_end = end_time + timedelta(seconds=self.clip_after_seconds)
            clip_duration = (clip_end - clip_start).total_seconds()

            clip_path = self.get_output_path(start_time, "visit", "mp4")
            logger.info(f"Creating visit clip: {clip_path.name}")

            success = self.capture.tee_manager.extract_clip(
                start_time=clip_start,
                duration_seconds=clip_duration,
                output_path=clip_path,
                fps=self.clip_fps,
                crf=self.clip_crf,
            )

            if success:
                logger.info(f"✅ Visit clip saved: {clip_path}")
            else:
                logger.warning("Failed to create visit clip")

        except Exception as e:
            logger.error(f"Error creating visit clip: {e}")

    def run(self) -> None:
        """Main monitoring loop using StreamCapture."""
        logger.info("=" * 60)
        logger.info("Starting Real-Time Falcon Monitoring")
        logger.info(f"Stream: {self.stream_url}")
        logger.info(f"Exit timeout: {self.exit_timeout}s")
        logger.info(f"Process interval: every {self.process_interval} frames")
        if self.max_runtime_seconds:
            logger.info(f"Test duration: {self.max_runtime_seconds}s")
        logger.info("=" * 60)

        logger.info("Press Ctrl+C to stop")

        # Pre-load YOLO model before starting stream to avoid memory fragmentation
        # Model uses lazy loading, so access it now before FFmpeg allocates memory
        logger.info("Pre-loading YOLO model...")
        _ = self.detector.model
        logger.info("✅ Model loaded successfully")

        start_time = time.time()

        try:
            # Use StreamCapture's frame iterator (handles reconnection)
            for frame in self.capture.frames(skip=self.process_interval):
                self.process_frame(frame.data)
                time.sleep(0.01)  # Prevent CPU spin

                # Check max runtime for test mode
                if self.max_runtime_seconds:
                    elapsed = time.time() - start_time
                    if elapsed >= self.max_runtime_seconds:
                        logger.info(
                            f"\n⏱️  Test duration complete: {self.max_runtime_seconds}s elapsed"
                        )
                        break

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
    """Entry point for real-time monitoring with CLI support."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Real-time falcon detection with live stream monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Production (24/7):  python -m kanyo.detection.realtime_monitor
  Test NSW 2 min:     python -m kanyo.detection.realtime_monitor --nsw --duration 2
  Test Harvard 5 min: python -m kanyo.detection.realtime_monitor --harvard --duration 5
  Custom config:      python -m kanyo.detection.realtime_monitor --config my_config.yaml
        """,
    )

    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--nsw",
        action="store_true",
        help="Use NSW Falcon Cam test config (test_config_nsw.yaml)",
    )
    parser.add_argument(
        "--harvard",
        action="store_true",
        help="Use Harvard Falcon Cam test config (test_config_harvard.yaml)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        metavar="MINUTES",
        help="Test mode: run for N minutes then exit (overrides max_runtime_seconds)",
    )

    args = parser.parse_args()

    # Determine config file based on flags
    if args.harvard:
        config_file = "test_config_harvard.yaml"
    elif args.nsw:
        config_file = "test_config_nsw.yaml"
    else:
        config_file = args.config

    # Load config
    config = load_config(config_file)
    setup_logging_from_config(config)
    logger = get_logger(__name__)

    # Apply test duration if specified
    if args.duration:
        config["max_runtime_seconds"] = args.duration * 60
        logger.info(f"⏱️  TEST MODE: Running for {args.duration} minutes")

    logger.info("=" * 80)
    logger.info("FALCON DETECTION MONITOR")
    logger.info("=" * 80)
    logger.info(f"Config file: {config_file}")
    logger.info(f"Stream: {config.get('video_source')}")
    logger.info(f"Detection confidence: {config.get('detection_confidence')}")
    logger.info(f"Frame interval: {config.get('frame_interval', 30)}")
    logger.info(f"Exit timeout: {config.get('exit_timeout', 120)}s")
    logger.info(f"Tee mode: {config.get('live_use_ffmpeg_tee', False)}")
    if args.duration:
        logger.info(f"Test duration: {args.duration} minutes")
    logger.info("=" * 80)

    monitor = None
    try:
        monitor = RealtimeMonitor(
            stream_url=config.get("video_source", DEFAULT_STREAM_URL),
            confidence_threshold=config.get("detection_confidence", 0.5),
            exit_timeout_seconds=config.get("exit_timeout", 300),
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
            max_runtime_seconds=config.get("max_runtime_seconds"),
            roosting_threshold=config.get("roosting_threshold", 1800),
            roosting_exit_timeout=config.get("roosting_exit_timeout", 600),
            activity_timeout=config.get("activity_timeout", 180),
            activity_notification=config.get("activity_notification", False),
        )
        # Configure notifications from config
        monitor.notifications = NotificationManager(config)
        monitor.run()
    except KeyboardInterrupt:
        logger.info("\n⏸️  Monitor interrupted by user")
    except Exception as e:
        logger.error(f"Monitor failed: {e}", exc_info=True)
        raise
    finally:
        if monitor:
            monitor.create_final_clip()
        logger.info("Monitor stopped")


if __name__ == "__main__":
    main()
