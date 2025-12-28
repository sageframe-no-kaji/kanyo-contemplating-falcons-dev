Here's the updated `sensing-logic.md`:

```markdown
# Kanyo Sensing Logic

> **This is the single source of truth for Kanyo's detection system.**

## Overview

Kanyo is a falcon detection system that monitors YouTube live streams of falcon nests, detects when birds are present, and intelligently tracks their behavior over time. The system uses a debounced state machine to eliminate false "entered/exited" spam that plagues simpler detection systems.

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

A **state machine** with debounced exit detection:

```
10:00:01 | FALCON ARRIVED
10:30:01 | FALCON ROOSTING (settled in, 30 min threshold)
13:00:00 | FALCON DEPARTED (3 hour visit)
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
│   │  • yt-dlp URL    │    │  • YOLO model    │    │  • 3 states     │  │
│   │  • OpenCV read   │    │  • Confidence    │    │  • Transitions  │  │
│   │  • Frame skip    │    │  • Class filter  │    │  • Timeouts     │  │
│   └────────┬─────────┘    └──────────────────┘    └────────┬────────┘  │
│            │                                                │          │
│            ▼                                                ▼          │
│   ┌──────────────────┐    ┌──────────────────┐    ┌─────────────────┐  │
│   │   FrameBuffer    │    │  VisitRecorder   │ ←  │    Events       │  │
│   │                  │    │                  │    │                 │  │
│   │  • Ring buffer   │ →  │  • Full visit    │    │  • ARRIVED      │  │
│   │  • 60s frames    │    │  • Arrival clip  │    │  • DEPARTED     │  │
│   │  • Pre-event     │    │  • Departure     │    │  • ROOSTING     │  │
│   └──────────────────┘    └──────────────────┘    └─────────────────┘  │
│                                                                         │
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

### Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `frame_interval` | 3 | Process every Nth frame (higher = less CPU) |
| `max_height` | 720 | Max resolution for YouTube streams |
| `reconnect_delay` | 5.0 | Seconds between reconnection attempts |

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

### Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `detection_confidence` | 0.35 | Minimum confidence to accept (0.0–1.0) |
| `model_path` | models/yolov8n.pt | Path to YOLO weights |
| `detect_any_animal` | true | Treat any animal as falcon |
| `animal_classes` | [14-23] | COCO class IDs to accept |

---

## Component 3: State Machine

**File**: `falcon_state.py`

### What It Does

The FalconStateMachine is the brain of the system. It tracks falcon presence over time and determines when meaningful events occur—eliminating the noise of raw frame-by-frame detections.

### The Three States

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
│ ROOSTING  │ ◄─── Long-term presence > 30 minutes (notification only)
└───────────┘
```

| State | Description | Exit Condition |
|-------|-------------|----------------|
| **ABSENT** | No falcon detected | Bird detected → VISITING |
| **VISITING** | Falcon present < 30 min | Gone 90s → DEPARTED, or stays 30 min → ROOSTING |
| **ROOSTING** | Falcon present > 30 min | Gone 90s → DEPARTED |

**Key insight**: ROOSTING uses the same exit timeout as VISITING. It exists only to trigger a "settled in" notification—not for different timeout behavior.

### State Transitions

#### ABSENT → VISITING

**Trigger**: Falcon detected after absence
**Event**: `ARRIVED`
**Actions**:
- Log arrival time
- Send arrival notification (with thumbnail)
- Start visit recording
- Start arrival clip recording (parallel, 45s duration)

#### VISITING → ROOSTING

**Trigger**: Continuous presence for 30 minutes (`roosting_threshold`)
**Event**: `ROOSTING`
**Actions**:
- Log transition to roosting state
- Send "settled in" notification (optional)
- No change to exit timeout

#### VISITING → ABSENT

**Trigger**: No detection for 90 seconds (`exit_timeout`)
**Event**: `DEPARTED`
**Actions**:
- Log departure with visit duration
- Send departure notification
- Stop visit recording
- Create departure clip from visit file

#### ROOSTING → ABSENT

**Trigger**: No detection for 90 seconds (`exit_timeout`)
**Event**: `DEPARTED`
**Actions**:
- Log departure with total duration
- Send departure notification
- Stop visit recording
- Create departure clip from visit file

### The Debounce (Rolling Timeout)

The exit timeout uses a **rolling debounce**:

```python
# Every frame where falcon NOT detected:
if last_absence_start is None:
    last_absence_start = now  # Start counting

absence_duration = now - last_absence_start

if absence_duration >= exit_timeout:
    # DEPARTED

# Every frame where falcon IS detected:
last_absence_start = None  # Reset the counter
```

If the bird disappears for 50 seconds then reappears, the counter **resets to zero**. The bird must be gone for a continuous 90 seconds to trigger departure.

### The Initialization Period

**Problem**: When monitoring starts, we don't know if a falcon is already present.

**Solution**: For the first 30 seconds, the system processes every frame and collects detections without triggering events. Then:
- If detections found → Initialize to ROOSTING (assume falcon was already there)
- If no detections → Initialize to ABSENT (nest is empty)

This prevents a false "ARRIVED" event when the monitor restarts with a falcon already present.

### Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `exit_timeout` | 90 | Seconds absent before departure (all states) |
| `roosting_threshold` | 1800 | Seconds (30 min) before ROOSTING notification |

---

## Component 4: Event Handling

**Files**: `event_handler.py`, `events.py`, `event_types.py`

### What It Does

FalconEventHandler receives events from the state machine and routes them to appropriate actions: notifications, thumbnails, and logging.

### Event Types

| Event | Meaning | Actions |
|-------|---------|---------|
| `ARRIVED` | Falcon entered after absence | Notify, thumbnail, start recording |
| `DEPARTED` | Falcon left | Notify, thumbnail, create clips |
| `ROOSTING` | Settled for long stay | Notify (optional), log |

### Event Persistence

Events are stored in JSON files organized by date:

```
clips/
└── 2025-12-28/
    ├── events_2025-12-28.json
    ├── falcon_081530_arrival.mp4
    ├── falcon_081530_arrival.jpg
    ├── falcon_104215_departure.mp4
    ├── falcon_104215_departure.jpg
    └── falcon_081530_visit.mp4
```

---

## Component 5: Buffer-Based Clip Extraction

**Files**: `frame_buffer.py`, `visit_recorder.py`, `buffer_clip_manager.py`, `arrival_clip_recorder.py`, `buffer_monitor.py`

### Architecture Overview

Kanyo uses a **buffer-based architecture** for clip extraction:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    BUFFER ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   ┌─────────────┐      ┌──────────────────┐      ┌───────────────┐ │
│   │ FrameBuffer │  →   │  VisitRecorder   │  →   │ BufferClip    │ │
│   │             │      │                  │      │ Manager       │ │
│   │ Ring buffer │      │ • Writes visit   │      │               │ │
│   │ of frames   │      │   video file     │      │ • Extracts    │ │
│   │ (60s)       │      │ • Logs events    │      │   departure   │ │
│   │             │      │ • Creates JSON   │      │   clips       │ │
│   └─────────────┘      └──────────────────┘      └───────────────┘ │
│         │                                                           │
│         │              ┌──────────────────┐                         │
│         └───────────── │ ArrivalClip      │                         │
│                        │ Recorder         │                         │
│                        │ • Parallel 45s   │                         │
│                        │ • Immediate clip │                         │
│                        └──────────────────┘                         │
└─────────────────────────────────────────────────────────────────────┘
```

### Components

**FrameBuffer** (`frame_buffer.py`)
- Thread-safe ring buffer storing recent frames (JPEG compressed)
- Configurable duration (default 60s)
- Frames retrieved by time range for pre-event footage

**VisitRecorder** (`visit_recorder.py`)
- Records complete falcon visits to video files
- Logs detection events with timestamps relative to video start
- Creates JSON metadata with visit summary
- Integrates pre-arrival frames from buffer

**ArrivalClipRecorder** (`arrival_clip_recorder.py`)
- Records arrival clips in parallel with visit recording
- Fixed duration (45s: 15s before + 30s after)
- Completes automatically, doesn't wait for departure

**BufferClipManager** (`buffer_clip_manager.py`)
- Orchestrates recording lifecycle
- Extracts departure clips from visit file using ffmpeg
- Uses `last_detection` time for accurate departure clip offset

**BufferMonitor** (`buffer_monitor.py`)
- Main entry point combining all components
- Captures frames → buffers → detects → manages clips

### Clip Strategy

| Clip Type | Before Event | After Event | When Created |
|-----------|--------------|-------------|--------------|
| **Arrival** | 15s | 30s | Immediately (parallel recording) |
| **Departure** | 60s | 30s | After visit file closes |
| **Full Visit** | All frames | All frames | Always saved |

### Departure Clip Timing

The departure clip is extracted from the visit file using the **last detection time**, not the end of the file:

```
Visit file: 3 hours 48 minutes
Bird last detected: 3 hours 38 minutes into file
Departure clip: (3h38m - 60s) to (3h38m + 30s)
```

This ensures the departure clip shows the bird leaving, not 10 minutes of empty nest.

### Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `buffer_duration` | 60 | Seconds of frames to keep in ring buffer |
| `clip_arrival_before` | 15 | Seconds before arrival for clip |
| `clip_arrival_after` | 30 | Seconds after arrival for clip |
| `clip_departure_before` | 60 | Seconds before departure for clip |
| `clip_departure_after` | 30 | Seconds after departure for clip |

---

## The Full Detection Loop

Here's what happens every processed frame (in `BufferMonitor`):

```python
def process_frame(frame, timestamp):
    # 1. Push frame into ring buffer (keeps 60s of history)
    frame_buffer.add_frame(frame, timestamp)

    # 2. Write to active recordings
    if visit_recorder.is_recording:
        visit_recorder.write_frame(frame)
    if arrival_clip_recorder.is_recording():
        arrival_clip_recorder.write_frame(frame, timestamp)

    # 3. Run YOLO detection
    detections = detector.detect_birds(frame, timestamp=now)
    falcon_detected = len(detections) > 0

    # 4. Update state machine → may generate events
    events = state_machine.update(falcon_detected, now)

    # 5. Handle events
    for event_type, event_time, metadata in events:
        if event_type == ARRIVED:
            # Get pre-arrival frames from buffer
            lead_in = frame_buffer.get_frames_before(event_time, 15)
            # Start parallel arrival clip (45s, auto-completes)
            arrival_clip_recorder.start_recording(event_time, lead_in)
            # Start long-term visit recording
            visit_recorder.start_recording(event_time, lead_in)

        if event_type == DEPARTED:
            # Stop visit recording
            visit_path, metadata = visit_recorder.stop_recording(event_time)
            # Extract departure clip from visit file
            clip_manager.create_departure_clip(metadata)
```

---

## Configuration Reference

### Full Configuration

```yaml
# ─────────────────────────────────────────────────────────────────────────────
# Stream & Detection
# ─────────────────────────────────────────────────────────────────────────────
video_source: "https://youtube.com/..."  # YouTube live stream URL
detection_confidence: 0.35               # 0.0–1.0, higher = fewer false positives
frame_interval: 3                        # Process every Nth frame
model_path: "models/yolov8n.pt"          # YOLO model
detect_any_animal: true                  # Treat any animal as falcon
timezone: "-05:00"                       # For timestamps and log files

# ─────────────────────────────────────────────────────────────────────────────
# State Machine
# ─────────────────────────────────────────────────────────────────────────────
exit_timeout: 90               # Seconds absent before departure (all states)
roosting_threshold: 1800       # 30 min - triggers ROOSTING notification

# ─────────────────────────────────────────────────────────────────────────────
# Buffer & Clip Extraction
# ─────────────────────────────────────────────────────────────────────────────
clips_dir: "clips"
buffer_duration: 60            # Seconds of frames in ring buffer
clip_arrival_before: 15        # Seconds before arrival in clip
clip_arrival_after: 30         # Seconds after arrival in clip
clip_departure_before: 60      # Seconds before departure in clip
clip_departure_after: 30       # Seconds after departure in clip
clip_crf: 23                   # Video quality (18=high, 23=balanced, 28=small)
clip_fps: 30                   # Output frame rate

# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────
telegram_enabled: true
telegram_channel: "@your_channel"
notification_cooldown_minutes: 5
```

---

## Tuning Guide

### `exit_timeout` (default: 90s)

| Situation | Recommendation |
|-----------|----------------|
| Reliable stream, good detection | 60–90s |
| Unreliable stream, detection gaps | 120–180s |
| Camera has occlusion issues | 120–180s |
| Want faster departure alerts | 60s |

**Note**: If too short, YOLO detection flicker causes false departures. If too long, you wait unnecessarily after bird leaves.

### `roosting_threshold` (default: 1800s / 30 min)

| Situation | Recommendation |
|-----------|----------------|
| Want "settled in" notification quickly | 900–1200s (15-20 min) |
| Only care about very long stays | 3600s (1 hour) |
| Don't want roosting notifications | Set very high or disable notification |

### `detection_confidence` (default: 0.35)

| Situation | Recommendation |
|-----------|----------------|
| Missing detections (bird present but not detected) | Lower to 0.25–0.30 |
| False positives (detecting shadows, etc.) | Raise to 0.40–0.50 |
| High-quality camera, good lighting | 0.35–0.45 |
| Poor camera, variable lighting | 0.25–0.35 |
```
