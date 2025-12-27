"""
Buffer-based real-time falcon detection monitor.

Uses in-memory frame buffer and visit recording for perfect clip timing.
No tee or segment files - simpler and more reliable.
"""

import os

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from kanyo.detection.buffer_clip_manager import BufferClipManager  # noqa: E402
from kanyo.detection.capture import StreamCapture  # noqa: E402
from kanyo.detection.detect import FalconDetector  # noqa: E402
from kanyo.detection.event_handler import FalconEventHandler  # noqa: E402
from kanyo.detection.events import EventStore, FalconVisit  # noqa: E402
from kanyo.detection.event_types import FalconEvent, FalconState  # noqa: E402
from kanyo.detection.falcon_state import FalconStateMachine  # noqa: E402
from kanyo.utils.config import load_config, get_now_tz  # noqa: E402
from kanyo.utils.frame_buffer import FrameBuffer  # noqa: E402
from kanyo.utils.logger import get_logger, setup_logging_from_config  # noqa: E402
from kanyo.utils.notifications import NotificationManager  # noqa: E402
from kanyo.utils.visit_recorder import VisitRecorder  # noqa: E402

logger = get_logger(__name__)

DEFAULT_STREAM_URL = "https://www.youtube.com/watch?v=glczTFRRAK4"


class BufferMonitor:
    """
    Real-time falcon monitor using in-memory buffer for clip extraction.

    Key differences from RealtimeMonitor:
    - Uses FrameBuffer instead of ffmpeg tee for pre-event footage
    - Uses VisitRecorder to record entire visits to video files
    - Clips extracted from visit files, not segment files
    - Perfect timing - the frame you detect IS the frame you save

    Orchestrates:
    - StreamCapture: video frame capture (no tee mode)
    - FalconDetector: YOLO-based detection
    - FrameBuffer: rolling buffer for pre-arrival footage
    - VisitRecorder: records entire visits
    - BufferClipManager: extracts clips from buffer/visits
    - FalconStateMachine: behavior tracking
    - EventStore: JSON persistence
    """

    def __init__(
        self,
        stream_url: str = DEFAULT_STREAM_URL,
        confidence_threshold: float = 0.5,
        exit_timeout_seconds: int = 120,
        process_interval_frames: int = 30,
        detect_any_animal: bool = True,
        animal_classes: list[int] | None = None,
        # Buffer settings
        buffer_seconds: int = 60,
        # Clip timing
        clip_arrival_before: int = 15,
        clip_arrival_after: int = 30,
        clip_departure_before: int = 30,
        clip_departure_after: int = 15,
        clip_state_change_before: int = 15,
        clip_state_change_after: int = 30,
        clip_state_change_cooldown: int = 300,
        clip_fps: int = 30,
        clip_crf: int = 23,
        clips_dir: str = "clips",
        # State machine settings
        roosting_threshold: int = 1800,
        roosting_exit_timeout: int = 600,
        activity_timeout: int = 180,
        activity_notification: bool = False,
        # Runtime settings
        max_runtime_seconds: int | None = None,
        full_config: dict | None = None,
    ):
        self.stream_url = stream_url
        self.exit_timeout = exit_timeout_seconds
        self.process_interval = process_interval_frames
        self.clip_fps = clip_fps
        self.clip_crf = clip_crf
        self.clips_dir = clips_dir
        self.max_runtime_seconds = max_runtime_seconds
        self.full_config = full_config or {}

        # Stream capture (NO tee mode)
        self.capture = StreamCapture(
            stream_url,
            use_tee=False,  # Key difference: no tee
        )

        # Detector
        self.detector = FalconDetector(
            confidence_threshold=confidence_threshold,
            detect_any_animal=detect_any_animal,
            animal_classes=animal_classes,
        )

        # Frame buffer for pre-arrival footage
        self.frame_buffer = FrameBuffer(
            buffer_seconds=buffer_seconds,
            fps=clip_fps,
        )

        # Visit recorder for full visit recordings
        self.visit_recorder = VisitRecorder(
            clips_dir=clips_dir,
            fps=clip_fps,
            crf=clip_crf,
            lead_in_seconds=clip_arrival_before,
            lead_out_seconds=clip_departure_after,
        )

        # Clip manager using buffer approach
        self.clip_manager = BufferClipManager(
            frame_buffer=self.frame_buffer,
            visit_recorder=self.visit_recorder,
            clips_dir=clips_dir,
            clip_fps=clip_fps,
            clip_crf=clip_crf,
            clip_arrival_before=clip_arrival_before,
            clip_arrival_after=clip_arrival_after,
            clip_departure_before=clip_departure_before,
            clip_departure_after=clip_departure_after,
            clip_state_change_before=clip_state_change_before,
            clip_state_change_after=clip_state_change_after,
            clip_state_change_cooldown=clip_state_change_cooldown,
        )

        # Event store
        date_str = get_now_tz(self.full_config).strftime("%Y-%m-%d")
        date_dir = Path(self.clips_dir) / date_str
        date_dir.mkdir(parents=True, exist_ok=True)
        events_path = date_dir / f"events_{date_str}.json"
        self.event_store = EventStore(events_path)

        # Event handler for notifications
        self.event_handler = FalconEventHandler(
            clips_dir=clips_dir,
            activity_notification=activity_notification,
        )

        # State machine
        self.state_machine = FalconStateMachine({
            "exit_timeout": exit_timeout_seconds,
            "roosting_threshold": roosting_threshold,
            "roosting_exit_timeout": roosting_exit_timeout,
            "activity_timeout": activity_timeout,
        })

        # State tracking
        self.current_visit: FalconVisit | None = None
        self.last_detection_time: datetime | None = None
        self._frame_size: tuple[int, int] | None = None

        logger.info("BufferMonitor initialized (no tee mode)")

    def process_frame(self, frame_data, frame_number: int) -> None:
        """Process a single frame for falcon detection."""
        try:
            now = get_now_tz(self.full_config)

            # Always add frame to buffer
            self.frame_buffer.add_frame(frame_data, now, frame_number)

            # Store frame size for recorder initialization
            if self._frame_size is None:
                h, w = frame_data.shape[:2]
                self._frame_size = (w, h)

            # If visit recording is active, write frame
            if self.visit_recorder.is_recording:
                self.visit_recorder.write_frame(frame_data)

            # Run detection
            detections = self.detector.detect_birds(frame_data, timestamp=now)
            falcon_detected = len(detections) > 0

            # Store frame for thumbnails
            if falcon_detected:
                self.event_handler.update_frame(frame_data)

            # Update state machine
            events = self.state_machine.update(falcon_detected, now)

            # Handle events
            for event_type, event_time, metadata in events:
                self.event_handler.handle_event(event_type, event_time, metadata)
                self._handle_event(event_type, event_time, metadata)

            # Check state change debounce
            self.clip_manager.check_state_change_debounce(now)

        except Exception as e:
            logger.error(f"❌ Error processing frame {frame_number}: {e}", exc_info=True)

    def _handle_event(
        self,
        event_type: FalconEvent,
        event_time: datetime,
        metadata: dict,
    ) -> None:
        """Handle detection events for recording and clips."""

        if event_type == FalconEvent.ARRIVED:
            # Start recording the visit
            logger.info("🦅 ARRIVAL - Starting visit recording")

            # Get lead-in frames from buffer
            lead_in_frames = self.frame_buffer.get_frames_before(
                event_time,
                self.visit_recorder.lead_in_seconds
            )

            self.visit_recorder.start_recording(
                arrival_time=event_time,
                lead_in_frames=lead_in_frames,
                frame_size=self._frame_size or (1280, 720),
            )

        elif event_type == FalconEvent.DEPARTED:
            # Stop recording and create clips
            logger.info("🦅 DEPARTURE - Stopping visit recording")

            visit_path, visit_metadata = self.visit_recorder.stop_recording(event_time)

            if visit_path and visit_metadata:
                # Create arrival and departure clips from the visit file
                self.clip_manager.create_arrival_clip(visit_metadata)
                self.clip_manager.create_departure_clip(visit_metadata)

                # Save visit metadata to event store
                if "visit_start" in metadata and "visit_end" in metadata:
                    visit = FalconVisit(
                        start_time=metadata["visit_start"],
                        end_time=metadata["visit_end"],
                    )
                    self.event_store.append(visit)

                    duration = visit_metadata.get("duration_seconds", 0)
                    logger.info(f"✅ Visit recorded: {duration:.0f}s → {visit_path}")

            # Cancel any pending state change clips
            self.clip_manager.cancel_pending_state_change()

        elif event_type in (FalconEvent.ROOSTING, FalconEvent.ACTIVITY_START, FalconEvent.ACTIVITY_END):
            # Log event in visit recording and schedule state change clip
            if self.visit_recorder.is_recording:
                self.visit_recorder.log_event(
                    event_type.name,
                    event_time,
                    metadata,
                )

                self.clip_manager.schedule_state_change_clip(
                    event_time=event_time,
                    event_name=event_type.name,
                    offset_seconds=self.visit_recorder.current_offset_seconds,
                )

    def run(self) -> None:
        """Main monitoring loop."""
        logger.info("=" * 60)
        logger.info("Starting Buffer-Based Falcon Monitoring")
        logger.info(f"Stream: {self.stream_url}")
        logger.info(f"Buffer: {self.frame_buffer.buffer_seconds}s @ {self.frame_buffer.fps}fps")
        logger.info(f"Exit timeout: {self.exit_timeout}s")
        logger.info(f"Process interval: every {self.process_interval} frames")
        if self.max_runtime_seconds:
            logger.info(f"Max runtime: {self.max_runtime_seconds}s")
        logger.info("=" * 60)
        logger.info("Press Ctrl+C to stop")

        # Pre-load YOLO model
        logger.info("Pre-loading YOLO model...")
        _ = self.detector.model
        logger.info("✅ Model loaded")

        start_time = time.time()
        initialization_complete = False
        initialization_duration = 30
        initial_detections = []
        max_birds_in_frame = 0
        last_heartbeat = time.time()
        heartbeat_interval = 300  # Log heartbeat every 5 minutes
        frames_processed = 0
        last_frame_time = time.time()
        frame_timeout = 60  # Warn if no frames for 60 seconds

        try:
            for frame in self.capture.frames(skip=0):
                frames_processed += 1
                last_frame_time = time.time()
                elapsed = time.time() - start_time

                # Initialization phase - process every frame
                if not initialization_complete:
                    if elapsed >= initialization_duration:
                        initialization_complete = True
                        falcon_detected = len(initial_detections) > 0

                        now = get_now_tz(self.full_config)
                        self.state_machine.initialize_state(falcon_detected, now)

                        state_name = self.state_machine.state.value
                        if falcon_detected:
                            max_conf = max(d.confidence for d in initial_detections)
                            logger.info(
                                f"📊 Initial state: {state_name.upper()} "
                                f"({max_birds_in_frame} bird(s), max conf: {max_conf:.2f})"
                            )

                            # Start recording since falcon is present
                            lead_in_frames = self.frame_buffer.get_frames_before(
                                now, self.visit_recorder.lead_in_seconds
                            )
                            self.visit_recorder.start_recording(
                                arrival_time=now,
                                lead_in_frames=lead_in_frames,
                                frame_size=self._frame_size or (1280, 720),
                            )
                        else:
                            logger.info(f"📊 Initial state: {state_name.upper()} (no birds)")

                        logger.info(f"🎯 Normal operation (every {self.process_interval} frames)")
                    else:
                        # Still initializing - detect but don't process events
                        now = get_now_tz(self.full_config)

                        # Add to buffer
                        self.frame_buffer.add_frame(frame.data, now, frame.frame_number)

                        # Store frame size
                        if self._frame_size is None:
                            h, w = frame.data.shape[:2]
                            self._frame_size = (w, h)

                        detections = self.detector.detect_birds(frame.data, timestamp=now)
                        if detections:
                            initial_detections.extend(detections)
                            max_birds_in_frame = max(max_birds_in_frame, len(detections))
                        continue

                # Normal operation - skip frames
                if initialization_complete:
                    if not hasattr(self, "_frame_counter"):
                        self._frame_counter = 0
                    self._frame_counter += 1

                    if self._frame_counter % (self.process_interval + 1) != 0:
                        # Still add to buffer even when skipping detection
                        now = get_now_tz(self.full_config)
                        self.frame_buffer.add_frame(frame.data, now, frame.frame_number)

                        # Still write to visit recording
                        if self.visit_recorder.is_recording:
                            self.visit_recorder.write_frame(frame.data)
                        continue

                    self.process_frame(frame.data, frame.frame_number)

                # Heartbeat logging
                now_time = time.time()
                if now_time - last_heartbeat >= heartbeat_interval:
                    state = self.state_machine.state.value
                    recording = "recording" if self.visit_recorder.is_recording else "monitoring"
                    logger.info(
                        f"💓 Heartbeat: {frames_processed} frames processed, "
                        f"state={state}, {recording}"
                    )
                    last_heartbeat = now_time

                # Watchdog: warn if no frames received recently
                time_since_frame = now_time - last_frame_time
                if time_since_frame > frame_timeout:
                    logger.warning(
                        f"⚠️  No frames received for {int(time_since_frame)}s - stream may be stalled"
                    )
                    last_frame_time = now_time  # Reset to avoid spam

                time.sleep(0.01)

                # Check max runtime
                if self.max_runtime_seconds:
                    if time.time() - start_time >= self.max_runtime_seconds:
                        logger.info(f"⏱️  Max runtime reached: {self.max_runtime_seconds}s")
                        break

        except KeyboardInterrupt:
            logger.info("\nStopping monitoring...")

        finally:
            # Stop visit recording if active
            if self.visit_recorder.is_recording:
                now = get_now_tz(self.full_config)
                visit_path, visit_metadata = self.visit_recorder.stop_recording(now)
                if visit_path:
                    logger.info(f"Final visit saved: {visit_path}")

            # Shutdown clip manager
            self.clip_manager.shutdown()

            # Disconnect stream
            self.capture.disconnect()

            logger.info("✅ Monitoring stopped")


def main():
    """Run the buffer-based monitor from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Buffer-based falcon monitor")
    parser.add_argument("config", nargs="?", default="config.yaml", help="Config file")
    parser.add_argument("--duration", type=int, help="Test duration in minutes")
    parser.add_argument("--harvard", action="store_true", help="Use Harvard config")
    parser.add_argument("--nsw", action="store_true", help="Use NSW config")
    args = parser.parse_args()

    # Determine config file
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

    # Apply test duration
    if args.duration:
        config["max_runtime_seconds"] = args.duration * 60
        logger.info(f"⏱️  Test mode: {args.duration} minutes")

    logger.info("=" * 80)
    logger.info("BUFFER-BASED FALCON MONITOR")
    logger.info("=" * 80)
    logger.info(f"Config: {config_file}")
    logger.info(f"Stream: {config.get('video_source')}")
    logger.info("=" * 80)

    try:
        monitor = BufferMonitor(
            stream_url=config.get("video_source", DEFAULT_STREAM_URL),
            confidence_threshold=config.get("detection_confidence", 0.5),
            exit_timeout_seconds=config.get("exit_timeout", 300),
            process_interval_frames=config.get("frame_interval", 30),
            detect_any_animal=config.get("detect_any_animal", True),
            animal_classes=config.get("animal_classes"),
            buffer_seconds=config.get("buffer_seconds", 60),
            clip_arrival_before=config.get("clip_arrival_before", 15),
            clip_arrival_after=config.get("clip_arrival_after", 30),
            clip_departure_before=config.get("clip_departure_before", 30),
            clip_departure_after=config.get("clip_departure_after", 15),
            clip_state_change_before=config.get("clip_state_change_before", 15),
            clip_state_change_after=config.get("clip_state_change_after", 30),
            clip_state_change_cooldown=config.get("clip_state_change_cooldown", 300),
            clip_fps=config.get("clip_fps", 30),
            clip_crf=config.get("clip_crf", 23),
            clips_dir=config.get("clips_dir", "clips"),
            roosting_threshold=config.get("roosting_threshold", 1800),
            roosting_exit_timeout=config.get("roosting_exit_timeout", 600),
            activity_timeout=config.get("activity_timeout", 180),
            activity_notification=config.get("activity_notification", False),
            max_runtime_seconds=config.get("max_runtime_seconds"),
            full_config=config,
        )

        # Configure notifications
        monitor.event_handler.notifications = NotificationManager(config)

        monitor.run()

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
