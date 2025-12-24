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

from kanyo.detection.capture import StreamCapture  # noqa: E402
from kanyo.detection.clip_manager import ClipManager  # noqa: E402
from kanyo.detection.detect import FalconDetector  # noqa: E402
from kanyo.detection.event_handler import FalconEventHandler  # noqa: E402
from kanyo.detection.events import EventStore, FalconVisit  # noqa: E402
from kanyo.detection.event_types import FalconEvent, FalconState  # noqa: E402
from kanyo.detection.falcon_state import FalconStateMachine  # noqa: E402
from kanyo.utils.config import load_config, get_now_tz  # noqa: E402
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
    - ClipManager: video clip extraction
    - FalconEventHandler: event routing and notifications
    - FalconStateMachine: behavior state tracking
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
        full_config: dict | None = None,  # Full config with timezone_obj
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

        # Store full config for timezone and other settings
        self.full_config = full_config or {}

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
        date_str = get_now_tz(self.full_config).strftime("%Y-%m-%d")
        date_dir = Path(self.clips_dir) / date_str
        date_dir.mkdir(parents=True, exist_ok=True)
        events_path = date_dir / f"events_{date_str}.json"
        self.event_store = EventStore(events_path)

        # Event handler manages notifications and event routing
        self.event_handler = FalconEventHandler(
            clips_dir=clips_dir,
            activity_notification=activity_notification,
        )

        # Clip manager handles video extraction
        self.clip_manager = ClipManager(
            tee_manager=self.capture.tee_manager if self.capture.tee_manager else None,
            clips_dir=clips_dir,
            clip_before_seconds=clip_before_seconds,
            clip_after_seconds=clip_after_seconds,
            clip_fps=clip_fps,
            clip_crf=clip_crf,
        )

        # State
        self.current_visit: FalconVisit | None = None
        self.last_detection_time: datetime | None = None
        self.arrival_clip_scheduled: datetime | None = (
            None  # Time when arrival clip should be created
        )

        # State machine for intelligent behavior tracking
        self.state_machine = FalconStateMachine({
            "exit_timeout": exit_timeout_seconds,
            "roosting_threshold": roosting_threshold,
            "roosting_exit_timeout": roosting_exit_timeout,
            "activity_timeout": activity_timeout,
        })

    def process_frame(self, frame) -> None:
        """Process a single frame for falcon detection."""
        now = get_now_tz(self.full_config)

        # Run YOLO detection (detect_birds filters by target_classes which includes animals)
        detections = self.detector.detect_birds(frame, timestamp=now)
        falcon_detected = len(detections) > 0

        # Store frame for thumbnails
        if falcon_detected:
            self.event_handler.update_frame(frame)

        # Update state machine with detection result
        events = self.state_machine.update(falcon_detected, now)

        # Handle any events generated by state machine
        for event_type, event_time, metadata in events:
            self.event_handler.handle_event(event_type, event_time, metadata)

            # Trigger clip creation for departure events
            if event_type == FalconEvent.DEPARTED and "visit_start" in metadata and "visit_end" in metadata:
                self.clip_manager.create_visit_clip(metadata["visit_start"], metadata["visit_end"])

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
        initialization_complete = False
        initialization_duration = 30  # Process every frame for first 30 seconds
        initial_detections = []  # Track detections during initialization

        try:
            # Use StreamCapture's frame iterator
            # During initialization: process EVERY frame (skip=0)
            # After initialization: use configured frame_interval (skip=self.process_interval)
            for frame in self.capture.frames(skip=0):
                elapsed = time.time() - start_time

                # During initialization (first 30 seconds), collect detections without processing state
                if not initialization_complete:
                    if elapsed >= initialization_duration:
                        # Initialization period complete
                        initialization_complete = True
                        falcon_detected = len(initial_detections) > 0

                        # Initialize state machine based on what we found
                        self.state_machine.initialize_state(falcon_detected, get_now_tz(self.full_config))

                        # Report initial state with details
                        state_name = self.state_machine.state.value
                        if falcon_detected:
                            max_conf = max(d.confidence for d in initial_detections)
                            bird_count = len(set(d.box_id for d in initial_detections))  # Unique detections
                            logger.info(
                                f"📊 Initial state after {initialization_duration}s: {state_name.upper()} "
                                f"({bird_count} bird{'s' if bird_count > 1 else ''} detected across {len(initial_detections)} detections, "
                                f"max confidence: {max_conf:.2f})"
                            )

                            # Generate initial clip if falcon is roosting
                            if self.clip_generator:
                                logger.info("📹 Generating clip for already-present falcon...")
                                self.clip_generator.generate_clip(
                                    frame.data,
                                    event_type="falcon_roosting_initial",
                                    timestamp=get_now_tz(self.full_config),
                                    description=f"Falcon already roosting at startup (confidence: {max_conf:.2f})"
                                )
                        else:
                            logger.info(f"📊 Initial state after {initialization_duration}s: {state_name.upper()} (no birds detected in {int(elapsed * 30)} frames)")

                        logger.info(f"🎯 Switching to normal operation (processing every {self.process_interval} frames)")
                        # Now switch to skipping frames - need to restart the iterator
                        # We'll use a flag to control frame skipping manually
                    else:
                        # Still initializing - process every frame
                        detections = self.detector.detect_birds(frame.data, timestamp=get_now_tz(self.full_config))
                        if detections:
                            logger.debug(f"🔍 Init {elapsed:.1f}s: Found {len(detections)} bird(s), max conf={max(d.confidence for d in detections):.2f}")
                            initial_detections.extend(detections)
                        continue  # Skip normal processing during initialization

                # After initialization: skip frames based on process_interval
                # We need to manually implement frame skipping since we can't change the iterator
                if initialization_complete:
                    # Simple frame counter for skipping
                    if not hasattr(self, '_frame_counter'):
                        self._frame_counter = 0
                    self._frame_counter += 1

                    if self._frame_counter % (self.process_interval + 1) != 0:
                        continue  # Skip this frame

                    # Process this frame
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
            if self.current_visit:
                self.clip_manager.create_final_clip(
                    get_now_tz(self.full_config),
                    self.event_handler.last_frame,
                )

            self.capture.disconnect()
            # Save any ongoing visit
            if self.current_visit is not None:
                self.current_visit.end_time = get_now_tz(self.full_config)
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
            full_config=config,  # Pass full config with timezone_obj
        )
        # Configure notifications from config
        monitor.event_handler.notifications = NotificationManager(config)
        monitor.run()
    except KeyboardInterrupt:
        logger.info("\n⏸️  Monitor interrupted by user")
    except Exception as e:
        logger.error(f"Monitor failed: {e}", exc_info=True)
        raise
    finally:
        if monitor and monitor.current_visit:
            monitor.clip_manager.create_final_clip(
                get_now_tz(monitor.full_config),
                monitor.event_handler.last_frame,
            )
        logger.info("Monitor stopped")


if __name__ == "__main__":
    main()
