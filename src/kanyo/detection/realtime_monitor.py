"""
Real-time falcon detection with notifications.

Monitors live stream continuously, sends alerts when falcons appear.
Uses StreamCapture for video, FalconDetector for inference,
and EventStore for persistence.
"""

import os

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from kanyo.detection.capture import StreamCapture  # noqa: E402
from kanyo.detection.clip_manager import ClipManager  # noqa: E402
from kanyo.detection.detect import FalconDetector  # noqa: E402
from kanyo.detection.event_handler import FalconEventHandler  # noqa: E402
from kanyo.detection.events import EventStore, FalconVisit  # noqa: E402
from kanyo.detection.event_types import FalconEvent  # noqa: E402
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
        # Legacy clip timing (for backward compatibility)
        clip_before_seconds: int = 30,
        clip_after_seconds: int = 60,
        # New event-specific clip timing
        clip_arrival_before: int = 15,
        clip_arrival_after: int = 30,
        clip_departure_before: int = 30,
        clip_departure_after: int = 15,
        clip_state_change_before: int = 15,
        clip_state_change_after: int = 30,
        clip_state_change_cooldown: int = 300,
        short_visit_threshold: int = 600,
        clip_fps: int = 30,
        clip_crf: int = 23,
        clips_dir: str = "clips",
        max_runtime_seconds: int | None = None,
        roosting_threshold: int = 1800,
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
        self.short_visit_threshold = short_visit_threshold

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
        )

        # Clip manager handles video extraction
        self.clip_manager = ClipManager(
            capture=self.capture,
            clips_dir=clips_dir,
            clip_before_seconds=clip_before_seconds,
            clip_after_seconds=clip_after_seconds,
            clip_arrival_before=clip_arrival_before,
            clip_arrival_after=clip_arrival_after,
            clip_departure_before=clip_departure_before,
            clip_departure_after=clip_departure_after,
            clip_state_change_before=clip_state_change_before,
            clip_state_change_after=clip_state_change_after,
            clip_state_change_cooldown=clip_state_change_cooldown,
            short_visit_threshold=short_visit_threshold,
            clip_fps=clip_fps,
            clip_crf=clip_crf,
        )

        # State
        self.current_visit: FalconVisit | None = None
        self.last_detection_time: datetime | None = None
        self.arrival_clip_scheduled: datetime | None = (
            None  # Time when arrival clip should be created
        )
        self.arrival_event_time: datetime | None = None  # Actual arrival time
        self.initial_clip_scheduled: datetime | None = (
            None  # Time when initial state clip should be created
        )
        self.initial_event_time: datetime | None = None  # Actual detection time

        # State machine for intelligent behavior tracking
        self.state_machine = FalconStateMachine(
            {
                "exit_timeout": exit_timeout_seconds,
                "roosting_threshold": roosting_threshold,
            }
        )

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

            # Schedule arrival clip creation (need to wait for "after" footage to exist)
            if event_type == FalconEvent.ARRIVED:
                # Store actual arrival time and schedule clip creation after buffer period
                self.arrival_event_time = event_time
                self.arrival_clip_scheduled = event_time + timedelta(
                    seconds=self.clip_manager.clip_arrival_after
                )
                logger.info(
                    f"📹 Arrival clip scheduled for {self.arrival_clip_scheduled.strftime('%H:%M:%S')}"
                )

            # Create departure/visit clips
            elif event_type == FalconEvent.DEPARTED:
                if "visit_start" in metadata and "visit_end" in metadata:
                    visit_start = metadata["visit_start"]
                    visit_end = metadata["visit_end"]
                    visit_duration = (visit_end - visit_start).total_seconds()

                    # Short visit? Save as one clip
                    if visit_duration < self.short_visit_threshold:
                        logger.info(
                            f"📹 Short visit ({visit_duration:.0f}s) - creating full visit clip"
                        )
                        scheduled = self.clip_manager.create_visit_clip(visit_start, visit_end)
                        if scheduled:
                            logger.info("✅ Visit clip scheduled (async)")
                        else:
                            logger.warning("❌ Visit clip creation failed")
                        # Cancel any scheduled arrival clip since visit clip covers it
                        self.arrival_clip_scheduled = None
                        self.arrival_event_time = None
                    else:
                        # Long visit - create departure clip only
                        # (arrival clip should have been created already)
                        logger.info(f"📹 Creating departure clip for visit ({visit_duration:.0f}s)")
                        scheduled = self.clip_manager.create_departure_clip(visit_end)
                        if scheduled:
                            logger.info("✅ Departure clip scheduled (async)")
                        else:
                            logger.warning("❌ Departure clip creation failed")
                else:
                    logger.warning("⚠️  Cannot create clip - missing timestamps")

            # State change clips (ROOSTING) - use debounce
            elif event_type == FalconEvent.ROOSTING:
                event_name = event_type.name
                self.clip_manager.schedule_state_change_clip(event_time, event_name)

            # Cancel pending state change clip on departure
            if event_type == FalconEvent.DEPARTED:
                self.clip_manager.cancel_pending_state_change()

        # Check if scheduled arrival clip is ready to be created
        if self.arrival_clip_scheduled and now >= self.arrival_clip_scheduled:
            logger.info("📹 Creating scheduled arrival clip (footage now available)")
            # Use the stored actual arrival time
            scheduled = self.clip_manager.create_arrival_clip(self.arrival_event_time)
            if scheduled:
                logger.info("✅ Arrival clip scheduled (async)")
            else:
                logger.warning("❌ Arrival clip creation failed")
            self.arrival_clip_scheduled = None
            self.arrival_event_time = None

        # Check if initial state clip is ready to be created (falcon present at startup)
        if self.initial_clip_scheduled and now >= self.initial_clip_scheduled:
            logger.info("📹 Creating initial state clip (footage now available)")
            # Use the stored actual detection time
            scheduled = self.clip_manager.create_initial_clip(self.initial_event_time)
            if scheduled:
                logger.info("✅ Initial state clip scheduled (async)")
            else:
                logger.warning("❌ Initial state clip creation failed")
            self.initial_clip_scheduled = None
            self.initial_event_time = None

        # Check if state change debounce has expired
        clip_scheduled = self.clip_manager.check_state_change_debounce(now)
        if clip_scheduled:
            logger.info("✅ State change clip scheduled (after debounce)")  # async

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
        max_birds_in_frame = 0  # Track maximum birds seen in any single frame

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
                        now = get_now_tz(self.full_config)
                        self.state_machine.initialize_state(falcon_detected, now)

                        # Report initial state with details
                        state_name = self.state_machine.state.value
                        if falcon_detected:
                            max_conf = max(d.confidence for d in initial_detections)
                            logger.info(
                                f"📊 Initial state after {initialization_duration}s: {state_name.upper()} "
                                f"({max_birds_in_frame} bird{'s' if max_birds_in_frame > 1 else ''} max per frame, "
                                f"{len(initial_detections)} total detections, "
                                f"max confidence: {max_conf:.2f})"
                            )

                            # Create initial state clip - captures what's happening when monitoring starts
                            # This is valuable when monitor restarts with falcon already present
                            logger.info(
                                "📹 Creating initial state clip (falcon present at startup)"
                            )
                            # Store detection time and schedule creation for after buffer period
                            self.initial_event_time = now
                            self.initial_clip_scheduled = now + timedelta(
                                seconds=self.clip_manager.clip_arrival_after
                            )
                            logger.info(
                                f"📹 Initial clip scheduled for {self.initial_clip_scheduled.strftime('%H:%M:%S')}"
                            )
                        else:
                            logger.info(
                                f"📊 Initial state after {initialization_duration}s: {state_name.upper()} (no birds detected in {int(elapsed * 30)} frames)"
                            )

                        logger.info(
                            f"🎯 Switching to normal operation (processing every {self.process_interval} frames)"
                        )
                        # Now switch to skipping frames - need to restart the iterator
                        # We'll use a flag to control frame skipping manually
                    else:
                        # Still initializing - process every frame
                        detections = self.detector.detect_birds(
                            frame.data, timestamp=get_now_tz(self.full_config)
                        )
                        if detections:
                            logger.debug(
                                f"🔍 Init {elapsed:.1f}s: Found {len(detections)} bird(s), max conf={max(d.confidence for d in detections):.2f}"
                            )
                            initial_detections.extend(detections)
                            max_birds_in_frame = max(max_birds_in_frame, len(detections))
                        continue  # Skip normal processing during initialization

                # After initialization: skip frames based on process_interval
                # We need to manually implement frame skipping since we can't change the iterator
                if initialization_complete:
                    # Simple frame counter for skipping
                    if not hasattr(self, "_frame_counter"):
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
            # Create final clip if falcon still present (synchronous - we wait)
            if self.current_visit:
                self.clip_manager.create_final_clip(
                    get_now_tz(self.full_config),
                    self.event_handler.last_frame,
                )

            # Shutdown clip manager - waits for any pending async clips
            self.clip_manager.shutdown()

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
            # Legacy clip timing (backward compatibility)
            clip_before_seconds=config.get("clip_before_seconds", 30),
            clip_after_seconds=config.get("clip_after_seconds", 60),
            # Event-specific clip timing
            clip_arrival_before=config.get("clip_arrival_before", 15),
            clip_arrival_after=config.get("clip_arrival_after", 30),
            clip_departure_before=config.get("clip_departure_before", 30),
            clip_departure_after=config.get("clip_departure_after", 15),
            clip_state_change_before=config.get("clip_state_change_before", 15),
            clip_state_change_after=config.get("clip_state_change_after", 30),
            clip_state_change_cooldown=config.get("clip_state_change_cooldown", 300),
            short_visit_threshold=config.get("short_visit_threshold", 600),
            clip_fps=config.get("clip_fps", 30),
            clip_crf=config.get("clip_crf", 23),
            clips_dir=config.get("clips_dir", "clips"),
            max_runtime_seconds=config.get("max_runtime_seconds"),
            roosting_threshold=config.get("roosting_threshold", 1800),
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
        # Ensure clip manager is shutdown (may already be done in run())
        if monitor:
            monitor.clip_manager.shutdown()
        logger.info("Monitor stopped")


if __name__ == "__main__":
    main()
