#!/usr/bin/env python3
"""Analyze sample video with debounce-based visit merging and clip extraction."""

from kanyo.detection.capture import StreamCapture
from kanyo.detection.detect import FalconDetector
from kanyo.detection.events import EventStore, FalconVisit
from kanyo.generation.clips import ClipExtractor
from kanyo.utils.config import load_config
from datetime import datetime
import argparse
import time
import os

FPS = 60  # Source video fps


def main():
    parser = argparse.ArgumentParser(description="Analyze video for falcon detection")
    parser.add_argument(
        "--extract-clips", action="store_true", help="Extract video clips around events"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what clips would be extracted without extracting",
    )
    parser.add_argument(
        "video",
        nargs="?",
        default="data/samples/falcon_full_test.mov",
        help="Video file to analyze",
    )
    args = parser.parse_args()

    config = load_config()
    frame_interval = config["frame_interval"]
    exit_timeout = config["exit_timeout"]
    merge_timeout = config.get("visit_merge_timeout", 60)

    print(f"Config: confidence={config['detection_confidence']}, frame_interval={frame_interval}")
    print(f"        exit_timeout={exit_timeout}s, merge_timeout={merge_timeout}s")
    print(f"        animal_classes={config.get('animal_classes', [14])}")

    video_path = args.video
    detector = FalconDetector(
        confidence_threshold=config["detection_confidence"],
        detect_any_animal=config["detect_any_animal"],
        animal_classes=config.get("animal_classes"),
    )

    if os.path.exists("data/falcon_config_test.json"):
        os.remove("data/falcon_config_test.json")
    store = EventStore(events_path="data/falcon_config_test.json")

    # Clip extractor for event-based clip extraction
    clip_extractor = ClipExtractor(config, video_path, fps=FPS)

    print(f"Processing: {video_path}")
    print("=" * 70)

    cap = StreamCapture(video_path)
    cap.connect()

    # Get total frames for progress
    total_frames = cap.total_frames
    video_duration = total_frames / FPS if total_frames > 0 else 0
    print(f"Video: {total_frames} frames, {video_duration:.0f}s ({video_duration/60:.1f} min)")
    print("=" * 70)

    # State tracking
    current_visit = None
    visit_start_frame = None  # Track video frame for duration calc
    visit_start_time = None  # Track timestamp for clip extraction
    last_detection_frame = None
    pending_exit_frame = None  # Frame where exit was first detected (debounce)
    frame_count = 0
    start_time = time.time()

    # Track completed visits for clip extraction
    completed_visits = []

    def frame_to_time(f):
        """Convert frame number to mm:ss string."""
        secs = f / FPS
        return f"{int(secs)//60:2d}:{int(secs)%60:02d}"

    def frames_to_secs(f):
        """Convert frame count to seconds."""
        return f / FPS

    def duration_str(start_frame, end_frame):
        """Calculate video duration from frame range."""
        secs = (end_frame - start_frame) / FPS
        mins = int(secs) // 60
        secs = int(secs) % 60
        return f"{mins}m {secs:02d}s"

    try:
        last_progress = 0
        while True:
            frame = cap.read_frame()
            if frame is None:
                break
            frame_count += 1

            # Show progress every 10%
            if total_frames > 0:
                progress = int(frame_count / total_frames * 100)
                if progress >= last_progress + 10:
                    video_time = frame_count / FPS
                    print(f"  ⏳ {progress}% ({int(video_time)//60}:{int(video_time)%60:02d})")
                    last_progress = progress

            if frame_count % frame_interval != 0:
                continue

            detections = detector.detect(frame.data)

            if detections:
                best = max(detections, key=lambda d: d.confidence)

                if current_visit is None:
                    # New visit
                    current_visit = FalconVisit(
                        start_time=datetime.now(),
                        peak_confidence=best.confidence,
                    )
                    visit_start_frame = frame_count
                    visit_start_time = datetime.now()
                    print(
                        f"🦅 ENTER  @ {frame_to_time(frame_count)} | frame {frame_count:5d} | conf: {best.confidence:.2f} | {best.class_name}"
                    )
                else:
                    # Continue or merge visit
                    if pending_exit_frame:
                        # Merge: re-entered within timeout, cancel pending exit
                        gap_secs = frames_to_secs(frame_count - pending_exit_frame)
                        print(
                            f"   ↳ merge @ {frame_to_time(frame_count)} | gap: {gap_secs:.1f}s | {best.class_name}"
                        )
                        pending_exit_frame = None
                    if best.confidence > current_visit.peak_confidence:
                        current_visit.peak_confidence = best.confidence

                last_detection_frame = frame_count

            elif current_visit and last_detection_frame:
                gap_frames = frame_count - last_detection_frame
                gap_secs = frames_to_secs(gap_frames)

                if pending_exit_frame is None and gap_secs >= exit_timeout:
                    # Start debounce timer
                    pending_exit_frame = last_detection_frame

                if pending_exit_frame:
                    debounce_secs = frames_to_secs(frame_count - pending_exit_frame)
                    if debounce_secs >= merge_timeout:
                        # Final exit - no re-entry within merge window
                        current_visit.end_time = datetime.now()
                        store.append(current_visit)
                        dur = duration_str(visit_start_frame, pending_exit_frame)
                        print(
                            f"🦅 EXIT   @ {frame_to_time(pending_exit_frame)} | frame {pending_exit_frame:5d} | dur: {dur} | peak: {current_visit.peak_confidence:.2f}"
                        )

                        # Track for clip extraction
                        completed_visits.append(
                            {
                                "enter_frame": visit_start_frame,
                                "exit_frame": pending_exit_frame,
                                "enter_time": visit_start_time,
                                "exit_time": datetime.now(),
                                "peak": current_visit.peak_confidence,
                            }
                        )

                        current_visit = None
                        visit_start_frame = None
                        visit_start_time = None
                        last_detection_frame = None
                        pending_exit_frame = None

    except KeyboardInterrupt:
        print("\n⚠️  Interrupted")

    cap.disconnect()

    # Handle visit still in progress
    if current_visit:
        current_visit.end_time = datetime.now()
        store.append(current_visit)
        exit_frame = pending_exit_frame or last_detection_frame or frame_count
        dur = duration_str(visit_start_frame, exit_frame)
        print(
            f"🦅 END    @ {frame_to_time(exit_frame)} | frame {exit_frame:5d} | dur: {dur} | peak: {current_visit.peak_confidence:.2f}"
        )

        completed_visits.append(
            {
                "enter_frame": visit_start_frame,
                "exit_frame": exit_frame,
                "enter_time": visit_start_time,
                "exit_time": datetime.now(),
                "peak": current_visit.peak_confidence,
            }
        )

    # Store video duration for clip extraction bounds
    video_duration_secs = frame_count / FPS
    clip_extractor.video_duration_secs = video_duration_secs

    elapsed = time.time() - start_time
    print("=" * 70)
    events = store.load()
    print(f"✅ Processed {frame_count} frames in {elapsed:.1f}s ({frame_count/elapsed:.0f} fps)")
    print(f"✅ Detected {len(events)} visit(s)")
    for i, e in enumerate(events, 1):
        print(f'   Visit {i}: peak {e["peak_confidence"]:.2f}')

    # Extract clips if requested
    if args.extract_clips or args.dry_run:
        print()
        print("=" * 70)
        print("CLIP EXTRACTION")
        print("=" * 70)

        for visit in completed_visits:
            clip_extractor.add_event("enter", visit["enter_frame"], visit["enter_time"])
            clip_extractor.add_event("exit", visit["exit_frame"], visit["exit_time"])

        clips = clip_extractor.plan_clips()
        print(f"Planned {len(clips)} clip(s):")
        for clip in clips:
            print(
                f"  - {clip.filename}: {clip.start_secs:.1f}s to {clip.end_secs:.1f}s ({clip.duration_secs:.1f}s)"
            )

        if args.extract_clips:
            print()
            extracted = clip_extractor.extract_clips(dry_run=args.dry_run)
            print(f"\n✅ Extracted {len(extracted)} clip(s)")


if __name__ == "__main__":
    main()
