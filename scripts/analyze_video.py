#!/usr/bin/env python3
"""Analyze sample video with debounce-based visit merging."""

from kanyo.detection.capture import StreamCapture
from kanyo.detection.detect import FalconDetector
from kanyo.detection.events import EventStore, FalconVisit
from kanyo.utils.config import load_config
from datetime import datetime
import time
import os

FPS = 60  # Source video fps

config = load_config()
frame_interval = config['frame_interval']
exit_timeout = config['exit_timeout']
merge_timeout = config.get('visit_merge_timeout', 60)

print(f"Config: confidence={config['detection_confidence']}, frame_interval={frame_interval}")
print(f"        exit_timeout={exit_timeout}s, merge_timeout={merge_timeout}s")
print(f"        animal_classes={config.get('animal_classes', [14])}")

video_path = 'data/samples/falcon_full_test.mov'
detector = FalconDetector(
    confidence_threshold=config['detection_confidence'],
    detect_any_animal=config['detect_any_animal'],
    animal_classes=config.get('animal_classes'),
)

if os.path.exists('data/falcon_config_test.json'):
    os.remove('data/falcon_config_test.json')
store = EventStore(events_path='data/falcon_config_test.json')

print(f'Processing: {video_path}')
print('=' * 70)

cap = StreamCapture(video_path)
cap.connect()

# State tracking
current_visit = None
visit_start_frame = None  # Track video frame for duration calc
last_detection_frame = None
pending_exit_frame = None  # Frame where exit was first detected (debounce)
frame_count = 0
start_time = time.time()

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
    while True:
        frame = cap.read_frame()
        if frame is None:
            break
        frame_count += 1

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
                print(f'🦅 ENTER  @ {frame_to_time(frame_count)} | frame {frame_count:5d} | conf: {best.confidence:.2f} | {best.class_name}')
            else:
                # Continue or merge visit
                if pending_exit_frame:
                    # Merge: re-entered within timeout, cancel pending exit
                    gap_secs = frames_to_secs(frame_count - pending_exit_frame)
                    print(f'   ↳ merge @ {frame_to_time(frame_count)} | gap: {gap_secs:.1f}s | {best.class_name}')
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
                    print(f'🦅 EXIT   @ {frame_to_time(pending_exit_frame)} | frame {pending_exit_frame:5d} | dur: {dur} | peak: {current_visit.peak_confidence:.2f}')
                    current_visit = None
                    visit_start_frame = None
                    last_detection_frame = None
                    pending_exit_frame = None

except KeyboardInterrupt:
    print('\n⚠️  Interrupted')

cap.disconnect()

if current_visit:
    current_visit.end_time = datetime.now()
    store.append(current_visit)
    exit_frame = pending_exit_frame or last_detection_frame or frame_count
    dur = duration_str(visit_start_frame, exit_frame)
    print(f'🦅 END    @ {frame_to_time(exit_frame)} | frame {exit_frame:5d} | dur: {dur} | peak: {current_visit.peak_confidence:.2f}')

elapsed = time.time() - start_time
print('=' * 70)
events = store.load()
print(f'✅ Processed {frame_count} frames in {elapsed:.1f}s ({frame_count/elapsed:.0f} fps)')
print(f'✅ Detected {len(events)} visit(s)')
for i, e in enumerate(events, 1):
    print(f'   Visit {i}: peak {e["peak_confidence"]:.2f}')
