"""
Buffer-based real-time falcon detection monitor.

Uses in-memory frame buffer and visit recording for perfect clip timing.
No tee or segment files - simpler and more reliable.
"""

import os
import signal

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
import statistics  # noqa: E402
import time  # noqa: E402
from concurrent.futures import Future  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from kanyo.detection.bird_count import BirdCountTracker  # noqa: E402
from kanyo.detection.buffer_clip_manager import BufferClipManager  # noqa: E402
from kanyo.detection.capture import StreamCapture  # noqa: E402
from kanyo.detection.detect import Detection, FalconDetector  # noqa: E402
from kanyo.detection.event_handler import FalconEventHandler  # noqa: E402
from kanyo.detection.event_types import FalconEvent  # noqa: E402
from kanyo.detection.events import EventStore, FalconVisit  # noqa: E402
from kanyo.detection.falcon_state import FalconStateMachine  # noqa: E402
from kanyo.detection.presence import PresenceTracker  # noqa: E402
from kanyo.detection.significance_filter import (  # noqa: E402
    EventSignificanceFilter,
    FilterDecision,
)
from kanyo.utils.arrival_clip_recorder import ArrivalClipRecorder  # noqa: E402
from kanyo.utils.config import get_now_tz, load_config  # noqa: E402
from kanyo.utils.frame_buffer import FrameBuffer  # noqa: E402
from kanyo.utils.logger import get_logger, setup_logging_from_config  # noqa: E402
from kanyo.utils.notifications import NotificationManager  # noqa: E402
from kanyo.utils.output import format_duration, get_output_path  # noqa: E402
from kanyo.utils.visit_recorder import VisitRecorder  # noqa: E402

logger = get_logger(__name__)

DEFAULT_STREAM_URL = "https://www.youtube.com/watch?v=glczTFRRAK4"

# Global flag for graceful shutdown (set by SIGTERM handler)
_shutdown_requested = False


def _handle_sigterm(signum, frame):
    """Handle SIGTERM for graceful Docker shutdown."""
    global _shutdown_requested
    logger.info("📥 Received SIGTERM - initiating graceful shutdown...")
    _shutdown_requested = True


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
        confidence_threshold_ir: float | None = None,
        exit_timeout_seconds: int = 120,
        process_interval_frames: int = 30,
        detect_any_animal: bool = True,
        animal_classes: list[int] | None = None,
        # Instrumentation settings
        detection_summary_interval: int = 300,
        # Buffer settings
        buffer_seconds: int = 60,
        # Clip timing
        clip_arrival_before: int = 15,
        clip_arrival_after: int = 30,
        clip_departure_before: int = 30,
        clip_departure_after: int = 15,
        clip_fps: int = 30,
        clip_crf: int = 23,
        clips_dir: str = "clips",
        # State machine settings
        roosting_threshold: int = 1800,
        # Stream recovery settings
        stream_recovery_threshold: int = 30,
        stream_recovery_confirmation: int = 10,
        stream_read_timeout_s: float = 10.0,
        # Presence layer settings (ho-12 / 024-C)
        presence_enabled: bool = True,
        presence_sustain_confidence: float = 0.15,
        presence_region_margin_frac: float = 0.25,
        presence_motion_pixel_threshold: int = 25,
        presence_motion_min_area_frac: float = 0.02,
        presence_global_change_frac: float = 0.5,
        presence_absence_failsafe_seconds: float = 3600.0,
        # Significance filter settings (ho-09 / 025-B). The constructor
        # default is False so direct construction (tests, embedders) keeps
        # today's surface behavior; production configs carry the DEFAULTS
        # (enabled) and main() wires them through.
        significance_filter_enabled: bool = False,
        merge_window_seconds: float = 300,
        min_significant_seconds: float = 30,
        damping_arrivals_threshold: int = 8,
        damping_window_hours: float = 1,
        # Bird count tracking (issue #3). Default False for safe rollout —
        # production configs opt in explicitly.
        bird_count_enabled: bool = False,
        bird_count_confirmation_seconds: float = 10.0,
        # Notification settings
        notify_on_startup: bool = True,
        record_arrival_on_startup: bool = False,
        # Runtime settings
        max_runtime_seconds: int | None = None,
        full_config: dict | None = None,
    ):
        self.stream_url = stream_url
        self.exit_timeout = exit_timeout_seconds
        self.process_interval = process_interval_frames
        self.notify_on_startup = notify_on_startup
        self.record_arrival_on_startup = record_arrival_on_startup
        self.clip_fps = clip_fps
        self.clip_crf = clip_crf
        self.clips_dir = clips_dir
        self.max_runtime_seconds = max_runtime_seconds
        self.full_config = full_config or {}

        # Stream recovery config
        self.stream_recovery_threshold = stream_recovery_threshold
        self.stream_recovery_confirmation = stream_recovery_confirmation

        # Detection-confidence summary instrumentation (022-E). One EVENT-level
        # rolling summary of detection polls every detection_summary_interval
        # seconds (0 disables). Data source for threshold tuning (ho-12+).
        self.detection_summary_interval = detection_summary_interval
        self._summary_poll_count = 0
        self._summary_detected_confidences: list[float] = []
        self._summary_window_start = time.time()

        # Stream capture (NO tee mode). Frames are stamped once, at read
        # time, in the reader thread — in the stream's configured timezone
        # so frame timestamps match every existing timestamp (ho-11).
        self.capture = StreamCapture(
            stream_url,
            use_tee=False,  # Key difference: no tee
            read_timeout_s=stream_read_timeout_s,
            now_fn=lambda: get_now_tz(self.full_config),
        )

        # Detector. With the presence layer enabled, the single inference per
        # poll runs at the sustain floor so the tracker sees the raw
        # (any-class, low-confidence) view; the filtered view keeps the
        # historical detect_birds() semantics (024-A). Disabled, the detector
        # is constructed exactly as before.
        self.detector = FalconDetector(
            confidence_threshold=confidence_threshold,
            confidence_threshold_ir=confidence_threshold_ir,
            detect_any_animal=detect_any_animal,
            animal_classes=animal_classes,
            raw_floor_confidence=(presence_sustain_confidence if presence_enabled else None),
        )

        # Presence layer (ho-12 / 024-C): sits between the detector and the
        # state machine and decides the boolean the state machine consumes.
        # None when disabled — the pipeline is then byte-identical to the
        # pre-presence behavior (falcon_detected = len(detections) > 0).
        self.presence: PresenceTracker | None = None
        if presence_enabled:
            self.presence = PresenceTracker(
                sustain_confidence=presence_sustain_confidence,
                region_margin_frac=presence_region_margin_frac,
                motion_pixel_threshold=presence_motion_pixel_threshold,
                motion_min_area_frac=presence_motion_min_area_frac,
                global_change_frac=presence_global_change_frac,
                absence_failsafe_seconds=presence_absence_failsafe_seconds,
            )
            # Semantic shift, logged once (ho-12): while present,
            # last_detection_time / visit_end reflect presence evidence
            # (sustain-level detections, region motion), not only YOLO hits.
            logger.info(
                "🔍 Presence layer ENABLED (ho-12): last_detection_time and visit_end "
                "reflect presence evidence, not only YOLO hits"
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
            stream_recovery_threshold=stream_recovery_threshold,
        )

        # Clip manager using buffer approach
        self.clip_manager = BufferClipManager(
            frame_buffer=self.frame_buffer,
            visit_recorder=self.visit_recorder,
            full_config=self.full_config,
            clips_dir=clips_dir,
            clip_fps=clip_fps,
            clip_crf=clip_crf,
            clip_arrival_before=clip_arrival_before,
            clip_arrival_after=clip_arrival_after,
            clip_departure_before=clip_departure_before,
            clip_departure_after=clip_departure_after,
        )

        # Event store - now determines file path based on event timestamp
        self.event_store = EventStore(
            clips_dir=clips_dir,
            timezone_config=full_config,
        )

        # Event handler for notifications
        self.event_handler = FalconEventHandler(
            clips_dir=clips_dir,
        )

        # State machine
        self.state_machine = FalconStateMachine(
            {
                "exit_timeout": exit_timeout_seconds,
                "roosting_threshold": roosting_threshold,
            }
        )

        # Significance filter (ho-09 / 025-B): the judgment layer between
        # state-machine events and their surface (notifications, event-store
        # rows, clip retention). Recording mechanics keep running on raw
        # events; only the surface flows through filter decisions. Disabled,
        # its pass-through decisions reproduce today's behavior exactly.
        self.significance_filter = EventSignificanceFilter(
            merge_window_seconds=merge_window_seconds,
            min_significant_seconds=min_significant_seconds,
            damping_arrivals_threshold=damping_arrivals_threshold,
            damping_window_hours=damping_window_hours,
            enabled=significance_filter_enabled,
        )
        if significance_filter_enabled:
            logger.info(
                "🔎 Significance filter ENABLED (ho-09): departures may surface up to "
                f"{merge_window_seconds:.0f}s late by design (merge window)"
            )
        # Bird count tracker (issue #3): a parallel judgment beside the
        # presence layer. None when disabled — the pipeline then carries no
        # count state at all and visit rows record max_concurrent_birds=null.
        self.bird_count: BirdCountTracker | None = None
        if bird_count_enabled:
            self.bird_count = BirdCountTracker(
                confirmation_seconds=bird_count_confirmation_seconds,
            )
            logger.info(
                "🔢 Bird count tracking ENABLED (issue #3): count changes confirm after "
                f"{bird_count_confirmation_seconds:.0f}s of sustained evidence"
            )
        # Visit-scoped max of the confirmed count (mirrors the peak-confidence
        # pattern, 022-A): written into the FalconVisit row on departure,
        # reset on every path that closes or cancels a visit.
        self._visit_max_concurrent: int = 0

        # The frame time driving filter decisions (ho-11 time authority):
        # set per processed frame; direct _handle_event calls fall back to
        # the event's own timestamp.
        self._frame_now: datetime | None = None
        # The visit row awaiting a released departure decision. Continuation
        # segments merge into it: the row is the unit of meaning, the visit
        # files remain the unit of storage.
        self._pending_visit_row: FalconVisit | None = None

        # State tracking
        self.current_visit: FalconVisit | None = None
        self.last_detection_time: datetime | None = None
        self._frame_size: tuple[int, int] | None = None

        # Visit-scoped peak detection confidence (022-A). Running max across
        # all detection polls of the current visit; written into the
        # FalconVisit row on departure. Reset on visit start and on every
        # path that closes or cancels a visit.
        self._visit_peak_confidence: float = 0.0
        # Max confidence of the most recent detection poll (0.0 when the poll
        # saw nothing) — used to seed the visit peak on ARRIVED.
        self._frame_peak_confidence: float = 0.0

        # Arrival clip recorder (short-duration, parallel to visit recorder)
        self.arrival_clip_recorder = ArrivalClipRecorder(self.clip_manager)
        # Arrival confirmation state
        self.arrival_pending = False
        self.arrival_pending_start: datetime | None = None
        self.arrival_detection_count = 0
        self.arrival_frame_count = 0

        # Startup confirmation state (similar to arrival, but no telegram notification)
        self.startup_pending = False
        self.startup_pending_start: datetime | None = None
        self.startup_detection_count = 0
        self.startup_frame_count = 0

        # Stream recovery confirmation state (check if bird still there after outage)
        self.recovery_pending = False
        self.recovery_pending_start: datetime | None = None
        self.recovery_detection_count = 0
        self.recovery_frame_count = 0
        # Latest detection timestamp seen during the recovery window — passed
        # to state_machine.confirm_recovery_presence so visit duration is not
        # inflated by the confirmation window length. See 021-J.
        self.recovery_latest_detection: datetime | None = None

        # Outage tracking driven by the no-frame sentinel from capture
        # (ho-11 / 023-B). The sentinel is the ONLY outage signal — the old
        # wall-clock gap heuristic was blind to a blocked read and
        # false-positived on slow processing. Duration is measured from the
        # last real frame's read-time stamp to the first frame after the
        # outage, matching the old gap semantics with trustworthy times.
        self.stream_read_timeout_s = stream_read_timeout_s
        self._outage_start: datetime | None = None
        self._outage_sentinel_count = 0
        self._last_frame_timestamp: datetime | None = None

        # Load arrival confirmation config
        self.arrival_confirmation_seconds = (
            full_config.get("arrival_confirmation_seconds", 10) if full_config else 10
        )
        self.arrival_confirmation_ratio = (
            full_config.get("arrival_confirmation_ratio", 0.3) if full_config else 0.3
        )

        # Roosting mode config
        self.roosting_recording_mode = (full_config or {}).get(
            "roosting_recording_mode", "continuous"
        )
        self.roosting_detection_interval = (full_config or {}).get(
            "roosting_detection_interval", 30
        )
        self.roosting_mode_active = False
        self.last_roosting_check: datetime | None = None
        self._roosting_visit_metadata: dict | None = None

        # Roosting-stop departure-candidate clip (022-C). At the first missed
        # roosting poll the departure window is still in the buffer, so a
        # candidate clip is snapshotted to the final departure path with a
        # .mp4.tmp suffix. DEPARTED finalizes it (rename); a re-confirming
        # bird discards it. Candidate = (extraction future, tmp path, final
        # path).
        self._roosting_last_poll_detected = False
        self._departure_candidate: tuple[Future[str | None], Path, Path] | None = None

        logger.info("BufferMonitor initialized (no tee mode)")

    def process_frame(self, frame_data, frame_number: int, timestamp: datetime) -> None:
        """Process a single frame for falcon detection.

        Args:
            frame_data: Frame image data
            frame_number: Frame sequence number
            timestamp: The frame's read-time stamp — the single time
                authority for everything derived from this frame (ho-11).
                Frames that queued during a stall carry the times they were
                actually read, so burst-stamping cannot occur here.
        """
        try:
            now = timestamp
            # Frame time drives significance-filter decisions too (025-B)
            self._frame_now = now

            # Always add frame to buffer (read time, not processing time)
            self.frame_buffer.add_frame(frame_data, now, frame_number)

            # Store frame size for recorder initialization
            if self._frame_size is None:
                h, w = frame_data.shape[:2]
                self._frame_size = (w, h)

            # If visit recording is active, write frame
            if self.visit_recorder.is_recording:
                self.visit_recorder.write_frame(frame_data)

                # Check for stream outage exceeded (>5 seconds of None frames)
                if self.visit_recorder.stream_outage_exceeded:
                    logger.warning(
                        "⚠️  Stream outage exceeded in process_frame - "
                        "stopping recording and resetting state"
                    )
                    self.visit_recorder.stop_recording(now)
                    self.arrival_clip_recorder.stop_recording(now)
                    self.state_machine.reset_to_absent()
                    self._reset_pending_states()
                    return

            # If arrival clip recording is active, write frame
            if self.arrival_clip_recorder.is_recording():
                self.arrival_clip_recorder.write_frame(frame_data, now)

            # In stop mode during roosting, poll YOLO at reduced interval
            if self.roosting_mode_active and self.roosting_recording_mode == "stop":
                if self.last_roosting_check is not None:
                    elapsed = (now - self.last_roosting_check).total_seconds()
                    if elapsed < self.roosting_detection_interval:
                        return  # skip YOLO this frame
                self.last_roosting_check = now

            # Run detection — exactly ONE inference per poll (ho-12).
            #
            # Presence enabled: the same inference yields the raw (any-class,
            # sustain-floor) view the tracker reasons over. `detections`
            # stays the filtered view for everything that inspects
            # confidences/boxes (peak tracking 022-A, summary 022-E,
            # thumbnails); `falcon_detected` becomes the tracker's judgment —
            # the boolean the state machine and visit lifetime consume.
            #
            # `yolo_detected` (filtered hits) keeps feeding the arrival/
            # startup/recovery confirmation counters: ho-12 keeps ENTER
            # exactly as strict as today ("feeding the existing arrival
            # confirmation window unchanged"), and the recovery window must
            # still be able to notice a bird that left during an outage —
            # fed the presence boolean, a parked-sustain would make those
            # windows vacuous.
            if self.presence is not None:
                detections, raw_detections = self.detector.detect_with_raw(
                    frame_data, timestamp=now
                )
                yolo_detected = len(detections) > 0
                falcon_detected = self.presence.update(frame_data, now, detections, raw_detections)
            else:
                # Legacy path — byte-identical to pre-presence behavior. The
                # raw view collapses onto the filtered one (no low floor), so
                # count candidates come from filtered detections only.
                detections = self.detector.detect_birds(frame_data, timestamp=now)
                raw_detections = detections
                yolo_detected = len(detections) > 0
                falcon_detected = yolo_detected

            # Track visit-scoped peak confidence (022-A) — keyed to filtered
            # detections, never to presence evidence.
            self._frame_peak_confidence = max((d.confidence for d in detections), default=0.0)
            if yolo_detected:
                self._visit_peak_confidence = max(
                    self._visit_peak_confidence, self._frame_peak_confidence
                )

            # Accumulate detection-confidence summary data (022-E) — a YOLO
            # threshold-tuning surface, so it counts filtered hits only.
            if self.detection_summary_interval > 0:
                self._summary_poll_count += 1
                if yolo_detected:
                    self._summary_detected_confidences.append(self._frame_peak_confidence)
                if time.time() - self._summary_window_start >= self.detection_summary_interval:
                    self._emit_detection_summary()

            # Roosting-stop departure-candidate snapshot/discard (022-C).
            # Runs before the state machine update so state_machine
            # .last_detection still refers to the last successful poll.
            if self.roosting_mode_active and self.roosting_recording_mode == "stop":
                if falcon_detected:
                    if not self._roosting_last_poll_detected and self._departure_candidate:
                        # Bird re-confirmed after a miss — the candidate was
                        # not a departure. Discard it; a later first-miss
                        # snapshots a fresh one.
                        logger.event(
                            "🦅 Roosting bird re-confirmed — discarding departure-candidate clip"
                        )
                        self._discard_departure_candidate()
                    self._roosting_last_poll_detected = True
                else:
                    if self._roosting_last_poll_detected:
                        # First missed poll — the departure window is still in
                        # the buffer. Snapshot it now; DEPARTED (if it fires)
                        # finalizes it, a re-confirmation discards it.
                        self._snapshot_departure_candidate(now)
                    self._roosting_last_poll_detected = False

            # Startup confirmation logic (similar to arrival, but no telegram until confirmed).
            # Counts filtered YOLO hits, not presence judgment — see the
            # detection block above.
            if self.startup_pending and self.startup_pending_start is not None:
                self.startup_frame_count += 1
                if yolo_detected:
                    self.startup_detection_count += 1

                elapsed = (now - self.startup_pending_start).total_seconds()
                if elapsed >= self.arrival_confirmation_seconds:
                    ratio = self.startup_detection_count / self.startup_frame_count

                    if ratio >= self.arrival_confirmation_ratio:
                        # SUCCESS - confirm startup presence
                        self._confirm_startup_presence(now)
                    else:
                        # FAILURE - not enough detections
                        self._cancel_startup_presence(ratio, now)
                        # The tracker was just reset with the state machine;
                        # the presence boolean computed at the top of this
                        # frame is stale. Fall back to this frame's YOLO
                        # evidence so a parked-sustain cannot instantly
                        # re-arrive off a reset (ho-12 / 024-C). Disabled,
                        # the two are already equal.
                        falcon_detected = yolo_detected

            # Arrival confirmation logic. Counts filtered YOLO hits, not
            # presence judgment — ENTER semantics stay exactly as strict as
            # today (ho-12).
            if self.arrival_pending and self.arrival_pending_start is not None:
                self.arrival_frame_count += 1
                if yolo_detected:
                    self.arrival_detection_count += 1

                elapsed = (now - self.arrival_pending_start).total_seconds()
                if elapsed >= self.arrival_confirmation_seconds:
                    ratio = self.arrival_detection_count / self.arrival_frame_count

                    if ratio >= self.arrival_confirmation_ratio:
                        # SUCCESS
                        self._confirm_arrival()
                    else:
                        # FAILURE
                        self._cancel_arrival(ratio, now)
                        # Stale presence boolean after a reset — see the
                        # startup cancel above (ho-12 / 024-C).
                        falcon_detected = yolo_detected

            # Recovery confirmation logic (after stream outage). Counts
            # filtered YOLO hits, not presence judgment — the outage hid any
            # exit motion from the tracker, so only fresh recognition can
            # prove the bird is still there.
            if self.recovery_pending and self.recovery_pending_start is not None:
                self.recovery_frame_count += 1
                if yolo_detected:
                    self.recovery_detection_count += 1
                    self.recovery_latest_detection = now

                elapsed = (now - self.recovery_pending_start).total_seconds()
                if elapsed >= self.stream_recovery_confirmation:
                    ratio = self.recovery_detection_count / max(self.recovery_frame_count, 1)

                    if ratio >= self.arrival_confirmation_ratio:
                        # SUCCESS - bird still present
                        self._confirm_recovery(now)
                    else:
                        # FAILURE - bird left during outage
                        self._cancel_recovery(ratio, now)
                        # Stale presence boolean after a reset — see the
                        # startup cancel above (ho-12 / 024-C).
                        falcon_detected = yolo_detected

            # Bird count tracking (issue #3): runs after the confirmation
            # blocks so a cancelled arrival's stale presence boolean has
            # already been corrected. Count changes surface through the
            # significance filter; the visit row records the max.
            self._update_bird_count(falcon_detected, detections, raw_detections, now)

            # Debug logging for detection tracking
            if falcon_detected:
                logger.debug(f"Bird detected at {now}, updating last_detection_time")
                self.last_detection_time = now
                # Mark current frame as having a detection (for accurate departure clip timing)
                if self.visit_recorder.is_recording:
                    self.visit_recorder.mark_detection()

            # Store frame for thumbnails
            if falcon_detected:
                self.event_handler.update_frame(frame_data)

            # Update state machine
            events = self.state_machine.update(falcon_detected, now)

            # Handle events. ARRIVED keeps deferring its surface to
            # confirmation; DEPARTED/ROOSTING notifications and event-store
            # rows now flow through the significance filter inside
            # _handle_event (ho-09 / 025-B) — recording mechanics still run
            # on the raw events immediately.
            for event_type, event_time, metadata in events:
                self._handle_event(event_type, event_time, metadata)

            # Advance the significance filter once per poll: release held
            # departures whose merge window expired and emit due damping
            # summaries (025-B).
            self._execute_decisions(self.significance_filter.tick(now))

        except Exception as e:
            logger.error(f"❌ Error processing frame {frame_number}: {e}", exc_info=True)

    def _update_bird_count(
        self,
        falcon_detected: bool,
        detections: list[Detection],
        raw_detections: list[Detection],
        now: datetime,
    ) -> None:
        """Derive the per-frame candidate count and feed the tracker (issue #3).

        Candidate derivation, strongest evidence first:

        - Presence absent → candidate 0 (the count lives inside a presence
          episode; the exit-timeout debounce means a wrong 0 here confirms
          only after sustained absence, and 0-boundary changes never surface
          as count notifications anyway).
        - Filtered (full-confidence) detections → their box count.
        - Sustain-level raw boxes with no filtered hit → their box count:
          continuity through the recognition dropouts the presence layer
          already tolerates.
        - Present with no boxes at all (parked bird) → no evidence; the
          confirmed count holds.

        Confirmed changes update the visit max; changes between nonzero
        counts route through the significance filter as COUNT_CHANGED (the
        0-boundary is the arrival/departure surface).
        """
        if self.bird_count is None:
            return

        candidate: int | None
        if not falcon_detected:
            candidate = 0
        elif detections:
            candidate = len(detections)
        elif raw_detections:
            candidate = len(raw_detections)
        else:
            candidate = None

        change = self.bird_count.update(candidate, now)
        if change is None:
            return

        self._visit_max_concurrent = max(self._visit_max_concurrent, change.new_count)
        logger.event(
            f"🔢 Bird count: {change.old_count} → {change.new_count} "
            f"(confirmed after {self.bird_count.confirmation_seconds:.0f}s sustained evidence)"
        )

        if change.old_count >= 1 and change.new_count >= 1:
            decisions = self.significance_filter.process(
                (
                    FalconEvent.COUNT_CHANGED,
                    change.timestamp,
                    {"old_count": change.old_count, "new_count": change.new_count},
                ),
                now,
            )
            self._execute_decisions(decisions)

    def _visit_max_birds(self) -> int | None:
        """The max_concurrent_birds value for a closing visit's row (issue #3).

        None when count tracking is disabled (the field stays additive and
        inert). A visit that closed before any count confirmation still had
        at least one bird — floor at 1.
        """
        if self.bird_count is None:
            return None
        return max(1, self._visit_max_concurrent)

    def _emit_detection_summary(self) -> None:
        """Emit the rolling detection-confidence summary and reset the window (022-E).

        One EVENT-level line per interval with a stable, parseable field order:
        poll count, detected count, detection ratio, and min/median/max
        confidence across detected polls (``conf=n/a`` when nothing was
        detected — the line still appears so gaps stay visible).
        """
        polls = self._summary_poll_count
        confidences = self._summary_detected_confidences
        detected = len(confidences)
        ratio = (detected / polls * 100) if polls else 0.0

        if detected:
            conf_fields = (
                f"conf min={min(confidences):.2f} "
                f"median={statistics.median(confidences):.2f} "
                f"max={max(confidences):.2f}"
            )
        else:
            conf_fields = "conf=n/a"

        logger.event(
            f"📊 Detection summary ({self.detection_summary_interval}s): "
            f"polls={polls} detected={detected} ratio={ratio:.1f}% {conf_fields}"
        )

        # Reset the accumulator for the next window
        self._summary_poll_count = 0
        self._summary_detected_confidences = []
        self._summary_window_start = time.time()

    def _snapshot_departure_candidate(self, now: datetime) -> None:
        """Snapshot a departure-candidate clip at the first missed roosting poll (022-C).

        The actual departure lies between the last successful roosting poll
        and this first miss — and right now that window is still in the
        buffer (polls are roosting_detection_interval apart, well inside
        buffer_seconds). The candidate covers
        [last_detection − clip_departure_before, now] and is written to the
        final departure path for last_detection with a .mp4.tmp suffix —
        last_detection at snapshot time is exactly what becomes visit_end if
        DEPARTED later fires, so the final name is already known.
        """
        last_detection = self.state_machine.last_detection
        if last_detection is None:
            logger.warning("Cannot snapshot departure candidate: no last_detection on hand")
            return

        # Any stale candidate should have been discarded on re-confirmation;
        # be defensive so miss/re-confirm cycles never accumulate .tmp files.
        if self._departure_candidate is not None:
            self._discard_departure_candidate()

        final_path = get_output_path(self.clips_dir, last_detection, "departure", "mp4")
        tmp_path = final_path.with_suffix(".mp4.tmp")
        start_time = last_detection - timedelta(seconds=self.clip_manager.clip_departure_before)

        future = self.clip_manager.extract_candidate_clip(start_time, now, tmp_path)
        self._departure_candidate = (future, tmp_path, final_path)

    def _discard_departure_candidate(self) -> None:
        """Discard an outstanding departure-candidate clip, deleting its .tmp file (022-C)."""
        candidate = self._departure_candidate
        self._departure_candidate = None
        if candidate is None:
            return

        future, tmp_path, _ = candidate
        future.cancel()
        try:
            # If extraction is still running, wait for it so the .tmp file is
            # not recreated after we delete it.
            future.result(timeout=30)
        except Exception:
            # Cancelled or extraction failed — either way only the file matters.
            pass

        try:
            if tmp_path.exists():
                tmp_path.unlink()
                logger.debug(f"Deleted departure-candidate clip: {tmp_path.name}")
        except OSError as e:
            logger.warning(f"Could not delete departure-candidate clip {tmp_path.name}: {e}")

    def _finalize_departure_candidate(self, event_time: datetime) -> bool:
        """Finalize the departure-candidate clip on a roosting-stop DEPARTED (022-C).

        Renames the candidate .mp4.tmp to its final .mp4 name, waiting on the
        extraction future (bounded) if it is still running. With no candidate
        on hand (e.g. process restarted mid-roost) falls back to the direct
        buffer extraction, whose window is by construction usually already
        evicted — the fallback is logged loudly and never fails the departure
        handling.

        Returns:
            True if a departure clip was produced/scheduled, False otherwise.
        """
        candidate = self._departure_candidate
        self._departure_candidate = None

        if candidate is None:
            logger.warning(
                "No departure-candidate clip on hand — falling back to direct buffer "
                "extraction; the clip window may already be evicted"
            )
            return self.clip_manager.create_clip_from_buffer(
                event_time,
                "departure",
                before_seconds=self.clip_manager.clip_departure_before,
                after_seconds=self.clip_manager.clip_departure_after,
            )

        future, tmp_path, final_path = candidate
        try:
            result = future.result(timeout=30)
        except Exception as e:
            logger.error(f"Departure-candidate extraction failed: {e}")
            result = None

        if result is None or not tmp_path.exists():
            logger.warning("Departure-candidate clip missing — no departure clip for this visit")
            return False

        try:
            tmp_path.rename(final_path)
        except OSError as e:
            logger.error(f"Could not finalize departure clip {tmp_path.name}: {e}")
            return False

        logger.event(f"✅ Departure clip finalized from candidate: {final_path.name}")
        return True

    def _handle_event(
        self,
        event_type: FalconEvent,
        event_time: datetime,
        metadata: dict,
    ) -> None:
        """Handle detection events for recording and clips."""

        if event_type == FalconEvent.ARRIVED:
            # Start arrival confirmation - don't notify yet
            logger.event(
                "🦅 FALCON ARRIVED at %s (stream local) - pending confirmation"
                % event_time.strftime("%I:%M:%S %p")
            )

            # Set pending state
            self.arrival_pending = True
            self.arrival_pending_start = event_time
            self.arrival_detection_count = 1
            self.arrival_frame_count = 1

            # New visit: discard any stale peak and seed with the arriving
            # frame's confidence (022-A); stale count max resets with it
            # (issue #3)
            self._visit_peak_confidence = self._frame_peak_confidence
            self._visit_max_concurrent = 0

            # Get lead-in frames from buffer
            lead_in_frames = self.frame_buffer.get_frames_before(
                event_time, self.visit_recorder.lead_in_seconds
            )

            # Start arrival clip recorder (short duration, completes automatically)
            self.arrival_clip_recorder.start_recording(
                arrival_time=event_time,
                lead_in_frames=lead_in_frames,
                frame_size=self._frame_size or (1280, 720),
            )

            # Start long-term visit recording (with same lead-in frames)
            self.visit_recorder.start_recording(
                arrival_time=event_time,
                lead_in_frames=lead_in_frames,
                frame_size=self._frame_size or (1280, 720),
            )

            # Do NOT call event_handler.handle_event() yet - wait for confirmation

        elif event_type == FalconEvent.DEPARTED:
            # Check if departure during pending confirmation
            if self.arrival_pending:
                # Bird left before confirmation - treat as failed confirmation
                ratio = self.arrival_detection_count / max(self.arrival_frame_count, 1)
                self._cancel_arrival(ratio, event_time)
                return  # Do not process departure normally

            # The visit is over — the tracker's presence episode ends with it,
            # so the next presence can only begin with a strict ENTER
            # (ho-12 / 024-C). Without this, a stale episode could flip back
            # to "present" on region motion alone after the departure.
            self._reset_presence()

            # Stop arrival clip if still active (short visit may not have hit its time limit)
            if self.arrival_clip_recorder.is_recording():
                self.arrival_clip_recorder.stop_recording(event_time)

            # Handle departure from roosting stop mode (visit recorder already stopped)
            if self.roosting_mode_active and self.roosting_recording_mode == "stop":
                logger.event("🦅 DEPARTURE from roost — finalizing departure-candidate clip")
                # Finalize the candidate snapshotted at the first missed poll
                # (022-C). The direct buffer extraction this replaces could
                # never work here: event_time is last_detection, which is
                # ≥ exit_timeout in the past — always outside the buffer.
                departure_clip_scheduled = self._finalize_departure_candidate(event_time)
                visit_row: FalconVisit | None = None
                if self._roosting_visit_metadata and "visit_start" in metadata:
                    visit_start_dt = metadata["visit_start"]
                    if isinstance(visit_start_dt, str):
                        visit_start_dt = datetime.fromisoformat(visit_start_dt)
                    last_detection_time = metadata.get("visit_end", event_time)
                    visit_end_dt = last_detection_time
                    if isinstance(visit_end_dt, str):
                        visit_end_dt = datetime.fromisoformat(visit_end_dt)
                    arrival_clip_path = get_output_path(
                        self.clips_dir, visit_start_dt, "arrival", "mp4"
                    )
                    thumbnail_path = get_output_path(
                        self.clips_dir, visit_start_dt, "arrival", "jpg"
                    )
                    # Departure clip path derived with the same expression the
                    # clip manager uses, so the paths agree by construction.
                    # Not gated on Path.exists() — extraction runs
                    # asynchronously on the clip manager's executor (022-A).
                    departure_clip_path = get_output_path(
                        self.clips_dir, visit_end_dt, "departure", "mp4"
                    )
                    visit_row = FalconVisit(
                        start_time=metadata["visit_start"],
                        end_time=last_detection_time,
                        peak_confidence=self._visit_peak_confidence,
                        arrival_clip_path=(
                            str(arrival_clip_path) if arrival_clip_path.exists() else None
                        ),
                        thumbnail_path=(str(thumbnail_path) if thumbnail_path.exists() else None),
                        departure_clip_path=(
                            str(departure_clip_path) if departure_clip_scheduled else None
                        ),
                        max_concurrent_birds=self._visit_max_birds(),
                    )
                self._visit_peak_confidence = 0.0
                self._visit_max_concurrent = 0
                self.roosting_mode_active = False
                self.last_roosting_check = None
                self._roosting_visit_metadata = None
                # Roosting mode over — reset candidate poll tracking (022-C).
                # The candidate itself was consumed by the finalize above.
                self._roosting_last_poll_detected = False
                # Surface (notification + event-store row) flows through the
                # significance filter (ho-09 / 025-B)
                self._route_departure_surface(event_time, metadata, visit_row)
                return

            # Stop recording and create clips
            logger.event("🦅 DEPARTURE - Stopping visit recording")

            visit_path, visit_metadata = self.visit_recorder.stop_recording(event_time)

            visit_row = None
            if visit_path and visit_metadata:
                # Use last_detection_time from state machine instead of departure_time
                # This ensures departure clip shows actual departure, not empty nest
                last_detection_time = metadata.get("visit_end", event_time)

                # Debug logging for clip timing
                visit_start = metadata.get("visit_start")
                logger.debug(f"Visit started: {visit_start}")
                logger.debug(f"Last detection: {last_detection_time}")
                if isinstance(visit_start, datetime) and isinstance(last_detection_time, datetime):
                    calculated_offset = (last_detection_time - visit_start).total_seconds()
                    logger.debug(f"Calculated offset: {calculated_offset:.1f} seconds")

                # Update visit_metadata with actual last_detection time
                visit_metadata["visit_end"] = last_detection_time

                # Create departure clip
                departure_clip_scheduled = self.clip_manager.create_departure_clip(visit_metadata)

                # Save visit metadata to event store
                if "visit_start" in metadata and "visit_end" in metadata:
                    visit_start_dt = metadata["visit_start"]
                    if isinstance(visit_start_dt, str):
                        visit_start_dt = datetime.fromisoformat(visit_start_dt)
                    visit_end_dt = metadata["visit_end"]
                    if isinstance(visit_end_dt, str):
                        visit_end_dt = datetime.fromisoformat(visit_end_dt)

                    # Derive arrival clip and thumbnail paths from arrival timestamp
                    arrival_clip_path = get_output_path(
                        self.clips_dir, visit_start_dt, "arrival", "mp4"
                    )
                    thumbnail_path = get_output_path(
                        self.clips_dir, visit_start_dt, "arrival", "jpg"
                    )
                    # Departure clip path derived with the same expression the
                    # clip manager uses, so the paths agree by construction.
                    # Not gated on Path.exists() — extraction runs
                    # asynchronously on the clip manager's executor (022-A).
                    departure_clip_path = get_output_path(
                        self.clips_dir, visit_end_dt, "departure", "mp4"
                    )

                    visit_row = FalconVisit(
                        start_time=metadata["visit_start"],
                        end_time=metadata["visit_end"],
                        peak_confidence=self._visit_peak_confidence,
                        arrival_clip_path=(
                            str(arrival_clip_path) if arrival_clip_path.exists() else None
                        ),
                        thumbnail_path=(str(thumbnail_path) if thumbnail_path.exists() else None),
                        departure_clip_path=(
                            str(departure_clip_path) if departure_clip_scheduled else None
                        ),
                        max_concurrent_birds=self._visit_max_birds(),
                    )

                    duration = visit_metadata.get("duration_seconds", 0)
                    logger.event(f"✅ Visit recorded: {duration:.0f}s → {visit_path}")

            # Visit is over — reset the visit-scoped peak (022-A) and count
            # max (issue #3)
            self._visit_peak_confidence = 0.0
            self._visit_max_concurrent = 0

            # Surface (notification + event-store row) flows through the
            # significance filter (ho-09 / 025-B). Runs even without a row —
            # the departure notification does not depend on the recorder
            # having produced a visit file.
            self._route_departure_surface(event_time, metadata, visit_row)

        elif event_type == FalconEvent.ROOSTING:
            if self.roosting_recording_mode == "stop":
                logger.event("🏠 Roosting mode=stop: finalizing visit recording")
                if self.visit_recorder.is_recording:
                    # Mark confirmed BEFORE stop so stop_recording() renames
                    # .mp4.tmp → .mp4 and updates metadata["visit_file"].
                    self.visit_recorder.rename_to_final()
                    _, self._roosting_visit_metadata = self.visit_recorder.stop_recording(
                        event_time
                    )
                self.roosting_mode_active = True
                self.last_roosting_check = event_time
                # Roosting starts on a detected poll — the candidate mechanism
                # (022-C) begins from a "detected" state.
                self._roosting_last_poll_detected = True
            else:
                # continuous mode: recording continues uninterrupted
                if self.visit_recorder.is_recording:
                    self.visit_recorder.log_event(
                        event_type.name,
                        event_time,
                        metadata,
                    )

            # ROOSTING surface passes through the filter untouched (ho-09):
            # notify=True, no event-store row — same as today, one path.
            self._execute_decisions(
                self.significance_filter.process(
                    (event_type, event_time, metadata), self._frame_now or event_time
                )
            )

    def _route_departure_surface(
        self,
        event_time: datetime,
        metadata: dict,
        visit_row: FalconVisit | None,
    ) -> None:
        """Route a DEPARTED's surface through the significance filter (025-B).

        Recording mechanics have already run on the raw event. The segment
        row merges into the pending row; the filter decides when (and how)
        the row and the departure notification surface.

        Args:
            event_time: The DEPARTED event time (visit_end).
            metadata: The raw state-machine event metadata.
            visit_row: The segment's FalconVisit row, or None when the
                recorder produced no visit file.
        """
        self._merge_pending_visit_row(visit_row)
        now = self._frame_now or event_time
        decisions = self.significance_filter.process(
            (FalconEvent.DEPARTED, event_time, metadata), now
        )
        self._execute_decisions(decisions)

    def _merge_pending_visit_row(self, visit_row: FalconVisit | None) -> None:
        """Merge a departure segment's row into the pending visit row (025-B).

        The row is the unit of meaning, the files the unit of storage: a
        continuation segment extends the pending row's span, takes the max
        peak confidence, and supplies the (new) departure clip; the arrival
        clip, thumbnail, start time, and id stay with the first segment.
        """
        if visit_row is None:
            return
        if self._pending_visit_row is None:
            self._pending_visit_row = visit_row
            return

        pending = self._pending_visit_row
        pending.end_time = visit_row.end_time
        pending.peak_confidence = max(pending.peak_confidence, visit_row.peak_confidence)
        pending.departure_clip_path = visit_row.departure_clip_path
        # Merged visit spans several segments: the row's max concurrent count
        # is the max across them (issue #3). None stays None (count disabled).
        if pending.max_concurrent_birds is not None or visit_row.max_concurrent_birds is not None:
            pending.max_concurrent_birds = max(
                pending.max_concurrent_birds or 1, visit_row.max_concurrent_birds or 1
            )

    def _execute_decisions(self, decisions: list[FilterDecision]) -> None:
        """Execute significance-filter decisions (025-B)."""
        for decision in decisions:
            self._execute_decision(decision)

    def _execute_decision(self, decision: FilterDecision) -> None:
        """Execute one significance-filter decision.

        Notifications go through the event handler; released departure rows
        pick up the filter's flags before the event-store append. Swallowed
        continuation arrivals (discard_arrival_clip) are zero-surface here —
        their clip discard happens at the confirmation site, where the
        recorder still holds the file.
        """
        if decision.is_summary:
            self._send_activity_summary(decision)
            return

        if decision.discard_arrival_clip:
            return

        if decision.event_type == FalconEvent.DEPARTED:
            if decision.notify:
                self.event_handler.handle_event(
                    FalconEvent.DEPARTED, decision.event_time, decision.metadata
                )
            row = self._pending_visit_row
            self._pending_visit_row = None
            if row is not None:
                row.insignificant = decision.insignificant
                row.merged_segments = decision.merged_segments
                if decision.record:
                    self.event_store.append(row)
        elif decision.event_type is not None and decision.notify:
            self.event_handler.handle_event(
                decision.event_type, decision.event_time, decision.metadata
            )

    def _send_activity_summary(self, decision: FilterDecision) -> None:
        """Send a damped-mode activity summary notification (ho-09 / 025-B)."""
        count = decision.metadata.get("count", 0)
        median_str = format_duration(decision.metadata.get("median_duration_seconds", 0.0))
        window_hours = decision.metadata.get("window_hours", 1)
        window_str = f"{window_hours:g} hour" + ("s" if window_hours != 1 else "")
        message = f"🦅 Busy nest: {count} visits in the last {window_str} (median {median_str})"
        logger.event(f"📊 Activity summary: {message}")

        notifications = self.event_handler.notifications
        if notifications:
            notifications.send_activity_summary(message)

    def _discard_continuation_arrival_clip(self, now: datetime) -> None:
        """Discard the continuation's arrival clip on a merged re-arrival (025-B).

        Called at confirmation time, when the arrival clip recorder is
        normally still writing: stop it without renaming and delete the .tmp
        (plus any already-final clip/thumbnail for that arrival, defensively).
        Data is not lost — the visit recording continues; only the redundant
        arrival clip of a continuation segment goes.

        Args:
            now: The driving frame timestamp.
        """
        tmp_path = self.arrival_clip_recorder.get_temp_path()
        final_paths: list[Path] = []
        if self.arrival_pending_start is not None:
            final_paths = [
                get_output_path(self.clips_dir, self.arrival_pending_start, "arrival", "mp4"),
                get_output_path(self.clips_dir, self.arrival_pending_start, "arrival", "jpg"),
            ]

        if self.arrival_clip_recorder.is_recording():
            self.arrival_clip_recorder.stop_recording(now)

        for path in [tmp_path, *final_paths]:
            if path and path.exists():
                try:
                    path.unlink()
                    logger.debug(f"Deleted continuation arrival file: {path.name}")
                except OSError as e:
                    logger.warning(f"Could not delete continuation arrival file {path.name}: {e}")

        logger.event("🔗 Continuation arrival clip discarded (merged visit)")

    def _confirm_arrival(self) -> None:
        """Confirm arrival after passing detection threshold."""
        ratio = self.arrival_detection_count / self.arrival_frame_count
        logger.event(
            f"✅ ARRIVAL CONFIRMED - detection ratio: {ratio:.1%} "
            f"({self.arrival_detection_count}/{self.arrival_frame_count} frames)"
        )

        # Route the confirmed arrival through the significance filter
        # (ho-09 / 025-B): a re-arrival inside the merge window is a
        # continuation — no notification, arrival clip discarded.
        decisions: list[FilterDecision] = []
        if self.arrival_pending_start is not None:
            now = self._frame_now or self.arrival_pending_start
            decisions = self.significance_filter.process(
                (FalconEvent.ARRIVED, self.arrival_pending_start, {}), now
            )

        if any(d.discard_arrival_clip for d in decisions):
            # Continuation of a merged visit: drop the arrival clip instead
            # of finalizing it. The visit recording still finalizes below —
            # a merged visit may span multiple visit files by design.
            self._discard_continuation_arrival_clip(
                self._frame_now or self.arrival_pending_start or get_now_tz(self.full_config)
            )
        else:
            # Rename arrival clip from .tmp to final
            self.arrival_clip_recorder.rename_to_final()

        # Rename visit recording from .tmp to final
        self.visit_recorder.rename_to_final()

        # NOW send the notification — if the filter's decision says so
        # (pass-through when the filter is disabled)
        self._execute_decisions(decisions)

        # Reset pending state
        self.arrival_pending = False
        self.arrival_pending_start = None
        self.arrival_detection_count = 0
        self.arrival_frame_count = 0

    def _cancel_arrival(self, ratio: float, now: datetime) -> None:
        """Cancel arrival - insufficient detections or early departure.

        Args:
            ratio: Detection ratio that failed the confirmation threshold
            now: The driving timestamp (frame read time or event time) —
                no re-stamping at processing time (ho-11).
        """
        logger.warning(
            f"⚠️  ARRIVAL CANCELLED - detection ratio: {ratio:.1%} "
            f"({self.arrival_detection_count}/{self.arrival_frame_count} frames, "
            f"threshold: {self.arrival_confirmation_ratio:.1%})"
        )

        # Capture paths before stopping (arrival_clip_recorder resets self._recorder on stop)
        arrival_tmp = self.arrival_clip_recorder.get_temp_path()
        visit_tmp = self.visit_recorder.get_temp_path()

        # Stop recordings WITHOUT renaming (keeps .tmp extension)
        self.arrival_clip_recorder.stop_recording(now)
        self.visit_recorder.stop_recording(now)  # Ignore return value

        # Clean up .tmp files from cancelled arrival
        for tmp_path in (arrival_tmp, visit_tmp):
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                    logger.debug(f"Deleted cancelled recording: {tmp_path.name}")
                except Exception as e:
                    logger.debug(f"Could not delete tmp file {tmp_path.name}: {e}")

        # Reset state machine to ABSENT
        self.state_machine.reset_to_absent()

        # Tracker resets with the state machine (ho-12 / 024-C)
        self._reset_presence()

        # Cancelled visit — discard its peak confidence (022-A) and count
        # max (issue #3)
        self._visit_peak_confidence = 0.0
        self._visit_max_concurrent = 0

        # Reset pending state
        self.arrival_pending = False
        self.arrival_pending_start = None
        self.arrival_detection_count = 0
        self.arrival_frame_count = 0

    def _confirm_startup_presence(self, now: datetime) -> None:
        """Confirm startup presence after passing detection threshold.

        Similar to _confirm_arrival but:
        - Transitions from PENDING_STARTUP to ROOSTING
        - Only sends notification if notify_on_startup is enabled

        Args:
            now: The driving frame timestamp (read time, ho-11).
        """
        ratio = self.startup_detection_count / self.startup_frame_count
        logger.event(
            f"✅ STARTUP PRESENCE CONFIRMED - detection ratio: {ratio:.1%} "
            f"({self.startup_detection_count}/{self.startup_frame_count} frames)"
        )

        # Rename arrival clip from .tmp to final
        self.arrival_clip_recorder.rename_to_final()

        # Rename visit recording from .tmp to final
        self.visit_recorder.rename_to_final()

        # Transition from PENDING_STARTUP to ROOSTING
        self.state_machine.confirm_startup_presence(now)

        # Send notification only if notify_on_startup is enabled — routed
        # through the significance filter like every confirmed arrival
        # (ho-09 / 025-B; nothing is ever held at startup, so this is a
        # pass-through that also seeds the damping arrival count)
        if self.notify_on_startup and self.startup_pending_start is not None:
            logger.info("📲 Sending startup arrival notification")
            decisions = self.significance_filter.process(
                (FalconEvent.ARRIVED, self.startup_pending_start, {"startup": True}),
                now,
            )
            self._execute_decisions(decisions)
        else:
            logger.info("📴 Skipping startup arrival notification (notify_on_startup=false)")

        # Reset pending state
        self.startup_pending = False
        self.startup_pending_start = None
        self.startup_detection_count = 0
        self.startup_frame_count = 0

    def _cancel_startup_presence(self, ratio: float, now: datetime) -> None:
        """Cancel startup presence - insufficient detections during confirmation window.

        Args:
            ratio: Detection ratio that failed the confirmation threshold
            now: The driving frame timestamp (read time, ho-11).
        """
        logger.warning(
            f"⚠️  STARTUP PRESENCE CANCELLED - detection ratio: {ratio:.1%} "
            f"({self.startup_detection_count}/{self.startup_frame_count} frames, "
            f"threshold: {self.arrival_confirmation_ratio:.1%})"
        )

        # Capture paths before stopping (arrival_clip_recorder resets self._recorder on stop)
        arrival_tmp = self.arrival_clip_recorder.get_temp_path()
        visit_tmp = self.visit_recorder.get_temp_path()

        # Stop recordings WITHOUT renaming (keeps .tmp extension)
        self.arrival_clip_recorder.stop_recording(now)
        self.visit_recorder.stop_recording(now)

        # Clean up .tmp files from cancelled startup
        for tmp_path in (arrival_tmp, visit_tmp):
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                    logger.debug(f"Deleted cancelled recording: {tmp_path.name}")
                except Exception as e:
                    logger.debug(f"Could not delete tmp file {tmp_path.name}: {e}")

        # Reset state machine to ABSENT
        self.state_machine.reset_to_absent()

        # Reset pending state
        self._reset_pending_states()

    def _confirm_recovery(self, now: datetime) -> None:
        """Confirm falcon still present after stream recovery.

        Transitions from PENDING_RECOVERY back to previous state (VISITING/ROOSTING).
        No notification - falcon never actually left.

        Args:
            now: The driving frame timestamp (read time, ho-11).
        """
        ratio = self.recovery_detection_count / max(self.recovery_frame_count, 1)
        logger.event(
            f"✅ RECOVERY CONFIRMED - falcon still present, ratio: {ratio:.1%} "
            f"({self.recovery_detection_count}/{self.recovery_frame_count} frames)"
        )

        # Confirm recovery in state machine (restores previous state).
        # Pass the actual latest detection time so visit duration isn't
        # inflated by the recovery window length (see 021-J).
        self.state_machine.confirm_recovery_presence(
            now, latest_detection_time=self.recovery_latest_detection
        )

        # Reset recovery pending state only
        self.recovery_pending = False
        self.recovery_pending_start = None
        self.recovery_detection_count = 0
        self.recovery_frame_count = 0
        self.recovery_latest_detection = None

    def _cancel_recovery(self, ratio: float, now: datetime) -> None:
        """Cancel recovery - falcon left during the stream outage.

        Generates DEPARTED event with proper clip timing.

        Args:
            ratio: Detection ratio that failed the confirmation threshold
            now: The driving frame timestamp (read time, ho-11).
        """
        logger.warning(
            f"⚠️  RECOVERY CANCELLED - falcon left during outage, ratio: {ratio:.1%} "
            f"({self.recovery_detection_count}/{self.recovery_frame_count} frames, "
            f"threshold: {self.arrival_confirmation_ratio:.1%})"
        )

        # Get departure events from state machine (uses last_detection from before outage)
        events = self.state_machine.cancel_recovery(now)

        # Tracker resets with the state machine (ho-12 / 024-C)
        self._reset_presence()

        # Handle departure event (create clips; the notification and row
        # flow through the significance filter inside _handle_event, 025-B)
        self._frame_now = now
        for event_type, event_time, metadata in events:
            self._handle_event(event_type, event_time, metadata)

        # Reset recovery pending state
        self.recovery_pending = False
        self.recovery_pending_start = None
        self.recovery_detection_count = 0
        self.recovery_frame_count = 0
        self.recovery_latest_detection = None

    def _reset_presence(self) -> None:
        """Reset the presence tracker so it cannot disagree with the state machine.

        Called wherever the state machine is force-reset (cancelled arrival,
        cancelled startup, cancelled recovery, outage-exceeded) and on a real
        DEPARTED — the tracker's episode ends with the visit, so a new
        presence can only begin with a strict ENTER (filtered detection).
        No-op when the presence layer is disabled (ho-12 / 024-C).

        The bird count tracker (issue #3) resets with it — a count only
        lives inside a presence episode, and its zero crossing is silent
        (the departure surface owns the 0-boundary).
        """
        if self.presence is not None:
            self.presence.reset()
        if self.bird_count is not None:
            self.bird_count.reset()

    def _reset_pending_states(self) -> None:
        """Reset all pending confirmation states (startup, arrival, and recovery)."""
        # Any visit in progress is being abandoned (cancelled startup or
        # outage-exceeded reset) — discard its peak confidence (022-A) and
        # count max (issue #3)
        self._visit_peak_confidence = 0.0
        self._visit_max_concurrent = 0

        # Tracker and state machine reset together (ho-12 / 024-C)
        self._reset_presence()

        self.startup_pending = False
        self.startup_pending_start = None
        self.startup_detection_count = 0
        self.startup_frame_count = 0

        self.arrival_pending = False
        self.arrival_pending_start = None
        self.arrival_detection_count = 0
        self.arrival_frame_count = 0

        self.recovery_pending = False
        self.recovery_pending_start = None
        self.recovery_detection_count = 0
        self.recovery_frame_count = 0
        self.recovery_latest_detection = None

    def _handle_no_frame_sentinel(self) -> None:
        """Consume a ``None`` sentinel from the capture (ho-11 / 023-B).

        Called from run() when frames() yields None — no frame arrived
        within stream_read_timeout_s, including when the underlying read is
        *blocked*, not just returning failure. This finally engages the
        long-dormant visit_recorder.write_frame(None) freeze-frame path and
        its stream_outage_exceeded accounting.

        Uses the wall clock: there is no frame to take a timestamp from —
        one of the legitimately non-frame contexts.
        """
        now = get_now_tz(self.full_config)

        if self._outage_start is None:
            self._outage_start = now
            logger.warning(
                f"⚠️  No frames for {self.stream_read_timeout_s:.0f}s - stream outage in progress"
            )
        self._outage_sentinel_count += 1

        if self.visit_recorder.is_recording:
            # Freeze-frame fill and outage accounting (existing contract —
            # consumed here, not modified).
            self.visit_recorder.write_frame(None)

            if self.visit_recorder.stream_outage_exceeded:
                logger.warning(
                    "⚠️  Stream outage exceeded during recording - "
                    "stopping recording and resetting state"
                )
                # Stop recording WITHOUT renaming (keeps .tmp extension)
                self.visit_recorder.stop_recording(now)
                self.arrival_clip_recorder.stop_recording(now)

                # Reset state machine and pending confirmations
                self.state_machine.reset_to_absent()
                self._reset_pending_states()

    def _handle_outage_recovery(self, now: datetime) -> None:
        """Run outage accounting on the first real frame after a sentinel stretch.

        Args:
            now: The first post-outage frame's read-time stamp.

        Outage duration is the gap between the last real frame's read time
        and this frame's read time (falling back to the first-sentinel wall
        time plus the read timeout when no frame was ever seen). Recovery
        confirmation entry is unchanged from before ho-11: bird present,
        outage within stream_recovery_threshold, and a recording active.
        """
        if self._last_frame_timestamp is not None:
            outage_duration = (now - self._last_frame_timestamp).total_seconds()
        else:
            # Outage from startup: no frame ever seen. The stretch began
            # ~read_timeout_s before the first sentinel.
            assert self._outage_start is not None
            outage_duration = (
                now - self._outage_start
            ).total_seconds() + self.stream_read_timeout_s

        sentinel_count = self._outage_sentinel_count
        self._outage_start = None
        self._outage_sentinel_count = 0

        self.state_machine.add_outage(outage_duration)
        logger.info(
            f"⚠️  Stream outage detected: {outage_duration:.1f}s ({sentinel_count} sentinel(s))"
        )

        # Check if bird was present before outage and outage is recoverable
        if (
            self.state_machine.is_falcon_present()
            and outage_duration <= self.stream_recovery_threshold
            and self.visit_recorder.is_recording
        ):
            # Short outage - start recovery confirmation
            self.state_machine.set_pending_recovery(now)

            self.recovery_pending = True
            self.recovery_pending_start = now
            self.recovery_detection_count = 0
            self.recovery_frame_count = 0
            self.recovery_latest_detection = None

            logger.info(
                f"🔄 Starting recovery confirmation "
                f"({self.stream_recovery_confirmation}s window)"
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

        # Register SIGTERM handler for graceful Docker shutdown
        signal.signal(signal.SIGTERM, _handle_sigterm)

        # Pre-load YOLO model
        logger.info("Pre-loading YOLO model...")
        _ = self.detector.model
        logger.info("✅ Model loaded")

        start_time = time.time()
        initialization_complete = False
        initialization_duration = 30
        initial_detections: list = []
        initial_frame_count = 0  # frames processed during init
        initial_detection_frame_count = 0  # init frames with >=1 detection
        max_birds_in_frame = 0
        last_heartbeat = time.time()
        heartbeat_interval = 300  # Log heartbeat every 5 minutes
        frames_processed = 0

        try:
            for frame in self.capture.frames(skip=0):
                # Check for graceful shutdown request (SIGTERM from Docker)
                if _shutdown_requested:
                    logger.info("🛑 Graceful shutdown requested...")
                    break

                # No-frame sentinel from the reader thread (ho-11 / 023-B).
                # The ONLY outage signal — no second watchdog in this loop.
                # Sentinels never reach frame processing.
                if frame is None:
                    self._handle_no_frame_sentinel()
                    continue

                frames_processed += 1
                current_time = time.time()

                # First real frame after an outage stretch: outage accounting
                # and (for short outages while recording) recovery
                # confirmation entry — same flow as before ho-11, now keyed
                # off sentinel data instead of a wall-clock gap heuristic
                # that was blind to blocked reads.
                if self._outage_start is not None:
                    if initialization_complete:
                        self._handle_outage_recovery(frame.timestamp)
                    else:
                        # Outage during startup init — no state to recover
                        # yet (parity with the pre-ho-11 init skip).
                        self._outage_start = None
                        self._outage_sentinel_count = 0

                self._last_frame_timestamp = frame.timestamp
                elapsed = current_time - start_time

                # Initialization phase - process every frame
                if not initialization_complete:
                    if elapsed >= initialization_duration:
                        initialization_complete = True
                        falcon_detected = len(initial_detections) > 0

                        # Frame read time drives the init transition too —
                        # single time authority (ho-11).
                        now = frame.timestamp
                        initial_state = self.state_machine.initialize_state(falcon_detected, now)

                        state_name = initial_state.value
                        if falcon_detected:
                            max_conf = max(d.confidence for d in initial_detections)

                            # Seed the visit peak from startup init detections (022-A)
                            self._visit_peak_confidence = max_conf

                            # Seed the presence tracker from the startup
                            # detections on entering PENDING_STARTUP
                            # (ho-12 / 024-C): the episode begins with a
                            # region from the best startup bbox and this
                            # frame as the motion baseline. The init loop
                            # itself stays on plain detection — no presence
                            # judgment before the state machine initializes.
                            if self.presence is not None:
                                self.presence.update(
                                    frame.data, now, initial_detections, initial_detections
                                )

                            # If falcon detected, state is PENDING_STARTUP
                            # We need to confirm presence before transitioning to ROOSTING
                            logger.info(
                                f"📊 Initial state: {state_name.upper()} "
                                f"({max_birds_in_frame} bird(s), max conf: {max_conf:.2f})"
                            )

                            # Start recording since falcon is present
                            lead_in_frames = self.frame_buffer.get_frames_before(
                                now, self.visit_recorder.lead_in_seconds
                            )

                            # Start arrival clip recorder only if enabled (short duration)
                            if self.record_arrival_on_startup:
                                self.arrival_clip_recorder.start_recording(
                                    arrival_time=now,
                                    lead_in_frames=lead_in_frames,
                                    frame_size=self._frame_size or (1280, 720),
                                )
                                logger.info(
                                    "📹 Recording startup arrival clip "
                                    "(record_arrival_on_startup=true)"
                                )
                            else:
                                logger.info(
                                    "⏭️  Skipping startup arrival clip "
                                    "(record_arrival_on_startup=false)"
                                )

                            # Start long-term visit recording (ALWAYS - needed for departure clips)
                            self.visit_recorder.start_recording(
                                arrival_time=now,
                                lead_in_frames=lead_in_frames,
                                frame_size=self._frame_size or (1280, 720),
                            )

                            # Start startup confirmation tracking (no telegram notification yet)
                            # Use same confirmation window as arrival confirmation.
                            # Seed with frame-based counts so ratio stays in [0, 1].
                            self.startup_pending = True
                            self.startup_pending_start = now
                            self.startup_detection_count = initial_detection_frame_count
                            self.startup_frame_count = initial_frame_count
                            logger.info(
                                f"⏳ Startup presence pending confirmation "
                                f"({self.arrival_confirmation_seconds}s window)"
                            )
                        else:
                            logger.info(f"📊 Initial state: {state_name.upper()} (no birds)")

                        logger.info(f"🎯 Normal operation (every {self.process_interval} frames)")
                    else:
                        # Still initializing - detect but don't process events
                        now = frame.timestamp

                        # Add to buffer
                        self.frame_buffer.add_frame(frame.data, now, frame.frame_number)

                        # Store frame size
                        if self._frame_size is None:
                            h, w = frame.data.shape[:2]
                            self._frame_size = (w, h)

                        detections = self.detector.detect_birds(frame.data, timestamp=now)
                        initial_frame_count += 1
                        if detections:
                            initial_detections.extend(detections)
                            initial_detection_frame_count += 1
                            max_birds_in_frame = max(max_birds_in_frame, len(detections))
                            # Store frame for startup notification photo
                            self.event_handler.update_frame(frame.data)
                        continue

                # Normal operation - skip frames
                if initialization_complete:
                    if not hasattr(self, "_frame_counter"):
                        self._frame_counter = 0
                    self._frame_counter += 1

                    if self._frame_counter % self.process_interval != 0:
                        # Still add to buffer even when skipping detection
                        # (read-time stamp — the single time authority, ho-11)
                        now = frame.timestamp
                        self.frame_buffer.add_frame(frame.data, now, frame.frame_number)

                        # Still write to visit recording
                        if self.visit_recorder.is_recording:
                            self.visit_recorder.write_frame(frame.data)

                            # Check for stream outage exceeded (>5 seconds of None frames)
                            if self.visit_recorder.stream_outage_exceeded:
                                logger.warning(
                                    "⚠️  Stream outage exceeded - stopping recording "
                                    "and resetting state"
                                )
                                # Stop recording WITHOUT renaming (keeps .tmp extension)
                                self.visit_recorder.stop_recording(now)
                                self.arrival_clip_recorder.stop_recording(now)

                                # Reset state machine and pending confirmations
                                self.state_machine.reset_to_absent()
                                self._reset_pending_states()

                        # Still write to arrival clip recording (fixes accelerated video bug)
                        if self.arrival_clip_recorder.is_recording():
                            self.arrival_clip_recorder.write_frame(frame.data, now)

                        continue

                    # Process with the frame's read-time stamp (ho-11)
                    self.process_frame(frame.data, frame.frame_number, frame.timestamp)

                # Heartbeat logging
                now_time = time.time()
                if now_time - last_heartbeat >= heartbeat_interval:
                    state = self.state_machine.state.value
                    recording = "recording" if self.visit_recorder.is_recording else "monitoring"
                    count_field = (
                        f", count={self.bird_count.confirmed_count}"
                        if self.bird_count is not None
                        else ""
                    )
                    logger.debug(
                        f"💓 Heartbeat: {frames_processed} frames processed, "
                        f"state={state}{count_field}, {recording}"
                    )
                    last_heartbeat = now_time

                # (No wall-clock frame watchdog here: the no-frame sentinel
                # from capture is the only outage signal — ho-11.)

                time.sleep(0.01)

                # Check max runtime
                if self.max_runtime_seconds:
                    if time.time() - start_time >= self.max_runtime_seconds:
                        logger.info(f"⏱️  Max runtime reached: {self.max_runtime_seconds}s")
                        break

        except KeyboardInterrupt:
            logger.info("\nStopping monitoring...")

        finally:
            logger.info("=" * 60)
            logger.info("🛑 KANYO SHUTTING DOWN")
            logger.info("=" * 60)

            # Stop visit recording if active - mark as confirmed so it gets renamed
            if self.visit_recorder.is_recording:
                now = get_now_tz(self.full_config)
                # Mark as confirmed so stop_recording will rename from .tmp to .mp4
                self.visit_recorder.rename_to_final()  # Sets _confirmed flag
                visit_path, visit_metadata = self.visit_recorder.stop_recording(now)
                if visit_path:
                    logger.info(f"✅ Final visit saved: {visit_path}")

            # Stop arrival clip recording if active
            if self.arrival_clip_recorder.is_recording():
                now = get_now_tz(self.full_config)
                self.arrival_clip_recorder.rename_to_final()  # Sets _confirmed flag
                self.arrival_clip_recorder.stop_recording(now)
                logger.info("✅ Final arrival clip saved")

            # Flush any held significance-filter decision so no row is lost
            # on SIGTERM (ho-09 / 025-B): a departure held for its merge
            # window is released immediately at shutdown.
            self._execute_decisions(self.significance_filter.flush(get_now_tz(self.full_config)))

            # Clean up an outstanding departure-candidate clip (022-C) —
            # no orphan .tmp files across restarts.
            self._discard_departure_candidate()

            # Shutdown clip manager
            self.clip_manager.shutdown()

            # Disconnect stream
            self.capture.disconnect()

            logger.info("=" * 60)
            logger.info("🛑 KANYO STOPPED")
            logger.info("=" * 60)


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
            confidence_threshold_ir=config.get("detection_confidence_ir"),
            exit_timeout_seconds=config.get("exit_timeout", 300),
            process_interval_frames=config.get("frame_interval", 30),
            detect_any_animal=config.get("detect_any_animal", True),
            animal_classes=config.get("animal_classes"),
            detection_summary_interval=config.get("detection_summary_interval", 300),
            buffer_seconds=config.get("buffer_seconds", 60),
            clip_arrival_before=config.get("clip_arrival_before", 15),
            clip_arrival_after=config.get("clip_arrival_after", 30),
            clip_departure_before=config.get("clip_departure_before", 30),
            clip_departure_after=config.get("clip_departure_after", 15),
            clip_fps=config.get("clip_fps", 30),
            clip_crf=config.get("clip_crf", 23),
            clips_dir=config.get("clips_dir", "clips"),
            roosting_threshold=config.get("roosting_threshold", 1800),
            stream_recovery_threshold=config.get("stream_recovery_threshold", 30),
            stream_recovery_confirmation=config.get("stream_recovery_confirmation", 10),
            stream_read_timeout_s=config.get("stream_read_timeout_s", 10.0),
            presence_enabled=config.get("presence_enabled", True),
            presence_sustain_confidence=config.get("presence_sustain_confidence", 0.15),
            presence_region_margin_frac=config.get("presence_region_margin_frac", 0.25),
            presence_motion_pixel_threshold=config.get("presence_motion_pixel_threshold", 25),
            presence_motion_min_area_frac=config.get("presence_motion_min_area_frac", 0.02),
            presence_global_change_frac=config.get("presence_global_change_frac", 0.5),
            presence_absence_failsafe_seconds=config.get("presence_absence_failsafe_seconds", 3600),
            significance_filter_enabled=config.get("significance_filter_enabled", True),
            merge_window_seconds=config.get("merge_window_seconds", 300),
            min_significant_seconds=config.get("min_significant_seconds", 30),
            damping_arrivals_threshold=config.get("damping_arrivals_threshold", 8),
            damping_window_hours=config.get("damping_window_hours", 1),
            bird_count_enabled=config.get("bird_count_enabled", False),
            bird_count_confirmation_seconds=config.get("bird_count_confirmation_seconds", 10),
            notify_on_startup=config.get("notify_on_startup", True),
            record_arrival_on_startup=config.get("record_arrival_on_startup", False),
            max_runtime_seconds=config.get("max_runtime_seconds"),
            full_config=config,
        )

        # Configure notifications
        monitor.event_handler.notifications = NotificationManager(config)

        # Set up admin alerts for stream connection issues (separate from public notifications)
        if monitor.event_handler.notifications:

            def send_connection_alert(message: str):
                monitor.event_handler.notifications.send_system_alert(message)

            monitor.capture.on_connection_issue = send_connection_alert

        monitor.run()

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
