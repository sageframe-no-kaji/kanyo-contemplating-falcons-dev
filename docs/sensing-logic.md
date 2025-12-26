# Kanyo Sensing Logic

> **This is the single source of truth for Kanyo's detection system.**
> Replaces the former `state-detection.md`.

## Overview

Kanyo is a falcon detection system that monitors YouTube live streams of falcon nests, detects when birds are present, and intelligently tracks their behavior over time. The system distinguishes between brief visits, long roosting sessions, and normal activity during roosting—eliminating the false "entered/exited" spam that plagues simpler detection systems.

### The Problem

Traditional detection systems trigger on every detection/no-detection transition:

```
10:00:01 | FALCON ENTERED
10:00:05 | FALCON EXITED    ← False (just moved out of frame)
10:00:08 | FALCON ENTERED   ← False (moved back)
10:00:12 | FALCON EXITED    ← False
...repeat 50 times during 3-hour roost...
```

### The Solution

A **state machine** that understands falcon behavior:

```
10:00:01 | FALCON ARRIVED
10:30:01 | FALCON ROOSTING (30 min threshold)
11:15:00 | FALCON ACTIVITY (brief absence during roost)
11:17:30 | FALCON SETTLED (back to roosting)
13:00:00 | FALCON DEPARTED (3 hour visit, 1 activity period)
```

**Result**: One arrival/departure pair for a 3-hour visit, instead of 100+ false alerts.

---

## The Detection Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     KANYO DETECTION PIPELINE                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   YouTube Stream                                                        │
│        │                                                                │
│        ▼                                                                │
│   ┌──────────────────┐    ┌──────────────────┐    ┌─────────────────┐  │
│   │  StreamCapture   │ →  │  FalconDetector  │ →  │ StateMachine    │  │
│   │  (capture.py)    │    │  (detect.py)     │    │ (falcon_state)  │  │
│   │                  │    │                  │    │                 │  │
│   │  • yt-dlp URL    │    │  • YOLO model    │    │  • 4 states     │  │
│   │  • OpenCV read   │    │  • Confidence    │    │  • Transitions  │  │
│   │  • Frame skip    │    │  • Class filter  │    │  • Timeouts     │  │
│   └──────────────────┘    └──────────────────┘    └────────┬────────┘  │
│                                                             │          │
│                                                             ▼          │
│   ┌──────────────────┐    ┌──────────────────┐    ┌─────────────────┐  │
│   │   ClipManager    │ ←  │  EventHandler    │ ←  │    Events       │  │
│   │                  │    │                  │    │                 │  │
│   │  • Arrival clip  │    │  • Notifications │    │  • ARRIVED      │  │
│   │  • Departure     │    │  • Thumbnails    │    │  • DEPARTED     │  │
│   │  • Short visit   │    │  • Logging       │    │  • ROOSTING     │  │
│   └──────────────────┘    └──────────────────┘    │  • ACTIVITY     │  │
│                                                    └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component 1: Frame Capture

**File**: `capture.py`

### What It Does

StreamCapture handles the connection to the video source and delivers individual frames to the detection system.

### How It Works

1. **URL Resolution**: For YouTube URLs, uses `yt-dlp` to resolve the live stream to a direct HLS/DASH URL that OpenCV can read.

2. **Frame Reading**: Opens a video capture with OpenCV and reads frames continuously.

3. **Frame Skipping**: The `frame_interval` setting controls how many frames to skip between detections. With a 30fps stream:

    - `frame_interval: 3` → Process every 3rd frame (~10 detections/sec)
    - `frame_interval: 30` → Process every 30th frame (~1 detection/sec)
4. **Reconnection**: If the stream drops, waits `reconnect_delay` seconds (default 5) then attempts to reconnect automatically.

5. **Tee Mode** (Optional): For continuous recording, can use ffmpeg to simultaneously proxy the stream for detection AND record rolling segment files for clip extraction.


### Configuration

|Setting|Default|Purpose|
|---|---|---|
|`frame_interval`|3|Process every Nth frame (higher = less CPU, coarser detection)|
|`max_height`|720|Max resolution for YouTube streams|
|`reconnect_delay`|5.0|Seconds between reconnection attempts|

---

## Component 2: YOLO Detection

**File**: `detect.py`

### What It Does

FalconDetector runs YOLOv8 inference on each frame to detect birds/animals.

### How It Works

1. **Model Loading**: Loads a YOLO model (default: `yolov8n.pt`, the "nano" model—fast but less accurate). Model is lazy-loaded on first detection to save memory.

2. **Inference**: Runs the YOLO model on each frame with the configured confidence threshold.

3. **Class Filtering**: YOLO detects 80 COCO classes. The system filters for relevant animals:

    - `detect_any_animal: true` (default) → Accepts any animal class (bird, cat, dog, etc.)
    - `detect_any_animal: false` → Only accepts class 14 (bird)
4. **Why "any animal"?**: The YOLO model often misclassifies falcons as cats, dogs, or other animals. On a dedicated falcon cam, any animal detection = falcon present.

5. **Detection Output**: Returns a list of `Detection` objects with class ID, confidence, bounding box, and timestamp.


### Configuration

|Setting|Default|Purpose|
|---|---|---|
|`detection_confidence`|0.3–0.5|Minimum confidence to accept (0.0–1.0)|
|`model_path`|models/yolov8n.pt|Path to YOLO weights|
|`detect_any_animal`|true|Treat any animal as falcon|
|`animal_classes`|[14-23]|COCO class IDs to accept|

### Detection Logic (Pseudocode)

```python
def detect(frame, confidence_threshold):
    results = yolo_model(frame, conf=confidence_threshold)

    detections = []
    for box in results.boxes:
        if box.class_id in target_classes:
            detections.append(Detection(box.class_id, box.confidence, box.bbox))

    return detections
```

---

## Component 3: State Machine

**File**: `falcon_state.py`

### What It Does

The FalconStateMachine is the brain of the system. It tracks falcon presence over time and determines when meaningful events occur—eliminating the noise of raw frame-by-frame detections.

### The Four States

```
┌─────────┐
│ ABSENT  │ ◄─── No falcon detected
└────┬────┘
     │ Detection
     ▼
┌──────────┐
│ VISITING │ ◄─── Falcon present < 30 minutes
└────┬─────┘
     │ 30 min threshold
     ▼
┌───────────┐
│ ROOSTING  │ ◄─── Long-term presence > 30 minutes
└────┬──────┘
     │ Brief absence (≥3 min)
     ▼
┌──────────┐
│ ACTIVITY │ ◄─── Movement during roost
└──────────┘
```

|State|Description|Exit Timeout|
|---|---|---|
|**ABSENT**|No falcon detected; waiting for arrival|N/A|
|**VISITING**|Falcon present < 30 min; short visit behavior|5 min|
|**ROOSTING**|Falcon present > 30 min; long-term stay|10 min|
|**ACTIVITY**|Brief absence (≥3 min) during roosting|10 min (inherits from roosting)|

### State Transitions

#### ABSENT → VISITING

**Trigger**: Falcon detected after absence
**Event**: `ARRIVED`
**Actions**:

- Log arrival time
- Send arrival notification (with thumbnail)
- Start visit timer
- Schedule arrival clip

#### VISITING → ROOSTING

**Trigger**: Continuous presence for 30 minutes (`roosting_threshold`)
**Event**: `ROOSTING`
**Actions**:

- Log transition to roosting state
- NO notification (internal event)
- Extend exit timeout from 5 → 10 minutes

#### VISITING → ABSENT

**Trigger**: No detection for 5 minutes (`exit_timeout`)
**Event**: `DEPARTED`
**Actions**:

- Log departure with visit duration
- Send departure notification
- Create departure clip (or full visit clip if short)
- Reset state

#### ROOSTING → ACTIVITY

**Trigger**: No detection for 3 minutes (`activity_timeout`)
**Event**: `ACTIVITY_START`
**Actions**:

- Log activity period start
- Optional notification (usually disabled)
- Track activity duration

#### ACTIVITY → ROOSTING

**Trigger**: Detection resumes
**Event**: `ACTIVITY_END`
**Actions**:

- Log activity period end
- Record activity duration
- Resume roosting monitoring

#### ROOSTING → ABSENT

**Trigger**: No detection for 10 minutes (`roosting_exit_timeout`)
**Event**: `DEPARTED`
**Actions**:

- Log departure with total duration and activity count
- Send departure notification
- Create complete visit clip
- Reset state

### The Initialization Period

**Problem**: When monitoring starts, we don't know if a falcon is already present.

**Solution**: For the first 30 seconds, the system processes every frame (ignoring `frame_interval`) and collects detections without triggering events. Then:

- If detections found → Initialize to ROOSTING (assume falcon was already roosting)
- If no detections → Initialize to ABSENT (nest is empty)

This prevents a false "ARRIVED" event when the monitor restarts with a falcon already present.

### Configuration

|Setting|Default|Purpose|
|---|---|---|
|`exit_timeout`|300s (5 min)|Departure threshold during VISITING|
|`roosting_threshold`|1800s (30 min)|Time before VISITING → ROOSTING|
|`roosting_exit_timeout`|600s (10 min)|Departure threshold during ROOSTING|
|`activity_timeout`|180s (3 min)|Absence duration to trigger ACTIVITY|
|`activity_notification`|false|Send notifications for activity events?|

### Timing Relationships

Critical constraints that must be maintained:

```
activity_timeout < roosting_exit_timeout
exit_timeout < roosting_exit_timeout
```

**Recommended ratios**:

```yaml
exit_timeout: 300              # 5 min (base unit)
activity_timeout: 180          # 3 min (60% of exit_timeout)
roosting_exit_timeout: 600     # 10 min (2x exit_timeout)
roosting_threshold: 1800       # 30 min (6x exit_timeout)
```

---

## Component 4: Event Handling

**Files**: `event_handler.py`, `events.py`, `event_types.py`

### What It Does

FalconEventHandler receives events from the state machine and routes them to appropriate actions: notifications, thumbnails, and logging.

### Event Types

|Event|Meaning|Actions|
|---|---|---|
|`ARRIVED`|Falcon entered after absence|Notify, thumbnail, schedule clip|
|`DEPARTED`|Falcon left|Notify, thumbnail, create clip|
|`ROOSTING`|Settled for long stay|Log only (no notification)|
|`ACTIVITY_START`|Movement during roost|Log (optional notify)|
|`ACTIVITY_END`|Settled after activity|Log only|

### Event Persistence

Events are stored in JSON files organized by date:

```
clips/
└── 2025-12-26/
    ├── events_2025-12-26.json
    ├── arrival_08-15-30.mp4
    ├── departure_10-42-15.mp4
    └── thumbnails/
```

Each `FalconVisit` record includes:

- Start/end timestamps
- Duration
- Peak confidence score
- Thumbnail and clip paths
- Activity period count

---

## Component 5: Clip Extraction

**Files**: `clip_manager.py`, `clips.py`

### Clip Strategy

|Clip Type|Before Event|After Event|Purpose|
|---|---|---|---|
|**Arrival**|15s|30s|Capture landing and settling|
|**Departure**|30s|15s|Capture what triggered exit|
|**Short Visit**|Full duration|Full duration|Single clip for visits < 10 min|
|**State Change**|15s|30s|Capture roosting↔activity transitions|

### Configuration

|Setting|Default|Purpose|
|---|---|---|
|`clip_arrival_before`|15|Seconds before arrival|
|`clip_arrival_after`|30|Seconds after arrival|
|`clip_departure_before`|30|Seconds before departure|
|`clip_departure_after`|15|Seconds after departure|
|`short_visit_threshold`|600 (10 min)|Max duration for single-clip visits|
|`clip_state_change_cooldown`|300 (5 min)|Prevent spam during fidgety periods|

---

## The Full Detection Loop

Here's what happens every processed frame:

```python
def process_frame(frame):
    # 1. Run YOLO detection
    detections = detector.detect_birds(frame, timestamp=now)
    falcon_detected = len(detections) > 0

    # 2. Store frame for thumbnails
    if falcon_detected:
        event_handler.update_frame(frame)

    # 3. Update state machine → may generate events
    events = state_machine.update(falcon_detected, now)

    # 4. Handle each event
    for event_type, event_time, metadata in events:
        event_handler.handle_event(event_type, event_time, metadata)

        # Schedule clip creation
        if event_type == ARRIVED:
            clip_manager.schedule_arrival_clip(event_time)
        elif event_type == DEPARTED:
            clip_manager.create_departure_clip(metadata)
```

---

## Configuration Reference

All settings in one place:

### Timing Constraints

The following relationships **must** hold for the state machine to work correctly:

```
activity_timeout < roosting_exit_timeout
exit_timeout < roosting_exit_timeout
roosting_threshold > exit_timeout
```

**Why these matter**:

| Constraint | If Violated |
|------------|-------------|
| `activity_timeout < roosting_exit_timeout` | Activity periods can never be detected; absence immediately becomes departure |
| `exit_timeout < roosting_exit_timeout` | Roosting offers no benefit over visiting; defeats purpose of extended timeout |
| `roosting_threshold > exit_timeout` | Falcon always departs before reaching roosting state; roosting never triggers |

These are now **enforced at config load time** and will raise `ValueError` if violated.

---

### Full Configuration

```yaml
# ─────────────────────────────────────────────────────────────────────────────
# Stream & Detection
# ─────────────────────────────────────────────────────────────────────────────
video_source: "https://youtube.com/..."  # YouTube live stream URL
detection_confidence: 0.3                # 0.0–1.0, higher = fewer false positives
frame_interval: 3                        # Process every Nth frame
model_path: "models/yolov8n.pt"          # YOLO model
detect_any_animal: true                  # Treat any animal as falcon
timezone: "-05:00"                       # For timestamps

# COCO class IDs (when detect_any_animal: true)
animal_classes: [14, 15, 16, 17, 18, 19, 20, 21, 22, 23]

# ─────────────────────────────────────────────────────────────────────────────
# State Machine Thresholds
# ─────────────────────────────────────────────────────────────────────────────
exit_timeout: 300              # 5 min - departure during short visit
roosting_threshold: 1800       # 30 min - transition to roosting
roosting_exit_timeout: 600     # 10 min - departure during roost
activity_timeout: 180          # 3 min - activity vs departure
activity_notification: false   # Notify on activity events?

# ─────────────────────────────────────────────────────────────────────────────
# Clip Extraction
# ─────────────────────────────────────────────────────────────────────────────
clips_dir: "clips"
clip_arrival_before: 15
clip_arrival_after: 30
clip_departure_before: 30
clip_departure_after: 15
short_visit_threshold: 600     # 10 min - one clip for short visits
clip_state_change_cooldown: 300

# Compression
clip_compress: true
clip_crf: 23                   # 18=high quality, 23=balanced, 28=small
clip_fps: 30
clip_hardware_encoding: true   # Auto-detect GPU encoder

# ─────────────────────────────────────────────────────────────────────────────
# Live Stream Ingestion
# ─────────────────────────────────────────────────────────────────────────────
live_use_ffmpeg_tee: true
live_proxy_url: "udp://127.0.0.1:12345"
buffer_dir: "/tmp/kanyo-buffer"
continuous_chunk_minutes: 10

# ─────────────────────────────────────────────────────────────────────────────
# Logging & Notifications
# ─────────────────────────────────────────────────────────────────────────────
log_level: "INFO"              # DEBUG for troubleshooting
log_file: "logs/kanyo.log"
telegram_enabled: true
telegram_channel: "@your_channel"
notification_cooldown_minutes: 5
```

---

## Tuning Guide

### Parameter Tuning Guidelines

#### `exit_timeout` (default: 300s / 5 min)

|Situation|Recommendation|
|---|---|
|High-quality stream, consistent detection|Shorter: 180–300s|
|Unreliable stream, detection gaps|Longer: 600–900s|
|Camera angle causes occlusion|Longer: 600–900s|
|Want immediate departure alerts|Shorter: 180–300s|

#### `roosting_threshold` (default: 1800s / 30 min)

|Situation|Recommendation|
|---|---|
|Falcons roost frequently|Shorter: 1200–1800s|
|Want to reserve roosting for overnight|Longer: 3600–5400s|
|Many false exits during long stays|Shorter: 1200–1800s|
|False roosting transitions|Longer: 3600s+|

#### `roosting_exit_timeout` (default: 600s / 10 min)

|Situation|Recommendation|
|---|---|
|Falcons rarely leave during roosting|Shorter: 480–600s|
|Brief hunting trips are normal|Longer: 900–1800s|
|Stream has periodic detection failures|Longer: 900–1200s|

**Must be > `exit_timeout`**

#### `activity_timeout` (default: 180s / 3 min)

|Situation|Recommendation|
|---|---|
|Falcons mostly stationary when roosting|Shorter: 120–180s|
|Falcons move frequently during roost|Longer: 300–600s|
|Seeing false activity periods|Shorter: 120s|

**Must be < `roosting_exit_timeout`**

### Environment-Specific Profiles

**Active Nest** (frequent short visits):

```yaml
exit_timeout: 180              # Fast detection (3 min)
roosting_threshold: 3600       # Rarely roost (60 min)
roosting_exit_timeout: 600     # Standard (10 min)
activity_timeout: 120          # Precise activity (2 min)
```

**Roosting Location** (long stays):

```yaml
exit_timeout: 600              # Tolerant of gaps (10 min)
roosting_threshold: 900        # Quick roosting (15 min)
roosting_exit_timeout: 1200    # Long tolerance (20 min)
activity_timeout: 300          # Lenient activity (5 min)
```

**Nesting Season** (spring/summer):

```yaml
roosting_threshold: 3600       # Less roosting, more activity
exit_timeout: 240              # Frequent short trips
activity_notification: true    # Track nesting behavior
```

**Migration/Winter**:

```yaml
roosting_threshold: 1200       # More roosting behavior
exit_timeout: 600              # Longer tolerance
activity_notification: false   # Less activity interest
```
