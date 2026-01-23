# Kanyo Sensing Logic

> **This is the single source of truth for Kanyo's detection system.**

---

## Plain English Summary

**The Problem:** Dumb detection systems send 100 alerts when a falcon sits still for 3 hours—triggering on every detection flicker.

**The Solution:** A state machine that waits and confirms before deciding anything.

### How It Works

1. **Detection**: YOLO looks at each frame and says "bird" or "no bird"
2. **Confirmation**: First detection starts a 10-second confirmation window—30% of frames must detect bird to confirm arrival
3. **State tracking**: Once confirmed, track whether bird is visiting (<30 min) or roosting (>30 min)
4. **Departure**: Bird must be gone for 90 continuous seconds before we declare departure
5. **Clips**: Record everything—arrival clips, departure clips, full visit videos

### The States

| State | Meaning | What Triggers Exit |
|-------|---------|-------------------|
| **ABSENT** | No bird | Detection → confirm → VISITING |
| **PENDING** | Maybe arrived, confirming... | Confirmed → VISITING, Failed → ABSENT |
| **VISITING** | Bird here < 30 min | 90s gone → DEPARTED, 30 min → ROOSTING |
| **ROOSTING** | Bird here > 30 min | 90s gone → DEPARTED |

### Why Confirmation Matters

Without confirmation, a single false-positive frame triggers a fake arrival notification. With confirmation:
- Real arrivals: 60%+ detection ratio → confirmed ✅
- False positives: <15% detection ratio → cancelled ❌

### The 90-Second Rule

The bird must be gone for 90 **continuous** seconds. If it flickers back into view at 89 seconds, the counter resets. This eliminates false departures from detection noise.

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
│   │  StreamCapture   │ →  │  FalconDetector  │ →  │  StateMachine   │  │
│   │  (capture.py)    │    │  (detect.py)     │    │  (falcon_state) │  │
│   │                  │    │                  │    │                 │  │
│   │  • yt-dlp URL    │    │  • YOLO model    │    │  • 4 states     │  │
│   │  • OpenCV read   │    │  • Confidence    │    │  • Confirmation │  │
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
| `detection_confidence` | 0.4 | Minimum confidence for daytime (0.0–1.0) |
| `detection_confidence_ir` | 0.2 | Minimum confidence for IR/night cameras |
| `model_path` | models/yolov8n.pt | Path to YOLO weights |
| `detect_any_animal` | true | Treat any animal as falcon |
| `animal_classes` | [14-23] | COCO class IDs to accept |

---

## Component 3: State Machine

**File**: `falcon_state.py`

### What It Does

The FalconStateMachine is the brain of the system. It tracks falcon presence over time and determines when meaningful events occur—eliminating the noise of raw frame-by-frame detections.

### The Four States

```
                      INITIALIZATION
                           │
              falcon detected on startup?
                      │           │
                      ▼           ▼
              PENDING_STARTUP   ABSENT ◄─────────────────┐
                      │           │                       │
                (confirm?)    detection                   │
                 │      │         │                       │
                 ▼      ▼         ▼                       │
             ROOSTING ABSENT   VISITING ──(30 min)──► ROOSTING
                                  │                       │
                                  └───────(90s gone)──────┘
```

| State | Description | Exit Condition |
|-------|-------------|----------------|
| **ABSENT** | No falcon detected | Bird detected → start confirmation |
| **PENDING_STARTUP** | Falcon detected on startup, confirming | Confirmed → ROOSTING, Failed → ABSENT |
| **VISITING** | Falcon present < 30 min | Gone 90s → DEPARTED, or stays 30 min → ROOSTING |
| **ROOSTING** | Falcon present > 30 min | Gone 90s → DEPARTED |

**Key insight**: ROOSTING uses the same exit timeout as VISITING. It exists only to trigger a "settled in" notification.

### Arrival Confirmation

When a falcon is first detected, the system doesn't immediately declare arrival. Instead:

1. **Start confirmation window** (10 seconds by default)
2. **Count detections** during the window
3. **Calculate ratio**: detections / total frames
4. **If ratio ≥ 30%**: Arrival confirmed → send notification
5. **If ratio < 30%**: Arrival cancelled → reset to ABSENT

This eliminates false arrivals from single-frame detection noise.

```
Detection → PENDING → (10 seconds) → ratio ≥ 30%? → CONFIRMED → VISITING
                                   → ratio < 30%? → CANCELLED → ABSENT
```

**During confirmation:**
- Recordings start immediately (with `.tmp` suffix)
- No notification sent yet
- If confirmed: rename `.tmp` files, send notification
- If cancelled: keep `.tmp` files for debugging, no notification

### Startup Confirmation

When the system starts and detects a falcon already present:

1. Enter `PENDING_STARTUP` state (not ROOSTING)
2. Run same confirmation logic as arrivals
3. If confirmed: transition to ROOSTING (optionally send notification if `notify_on_startup: true`)
4. If not confirmed: transition to ABSENT

This prevents false "arrived" notifications when restarting the container with a falcon already on camera.

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

### Stream Outage Handling

When the YouTube stream drops:

1. **Brief outages (<5 seconds)**: Use "freeze frame" — repeat last good frame to maintain video continuity
2. **Extended outages (>5 seconds)**:
   - Stop recordings (prevents corrupted video)
   - Track cumulative outage time (doesn't count toward absence duration)
   - Reset state to ABSENT
   - When stream resumes with detection, go through normal confirmation flow

### Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `exit_timeout` | 90 | Seconds absent before departure (all states) |
| `roosting_threshold` | 1800 | Seconds (30 min) before ROOSTING notification |
| `arrival_confirmation_seconds` | 10 | Confirmation window duration |
| `arrival_confirmation_ratio` | 0.3 | Required detection ratio to confirm (30%) |
| `notify_on_startup` | false | Send notification if falcon present on startup |

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
│   │             │      │                  │      │    Manager    │ │
│   │  Ring buffer│      │  • Write visit   │      │               │ │
│   │  (60s)      │      │  • Track times   │      │  • Departure  │ │
│   │             │      │  • Metadata      │      │    clips      │ │
│   └─────────────┘      └──────────────────┘      └───────────────┘ │
│         │                                                           │
│         │              ┌──────────────────┐                        │
│         └───────────── │ ArrivalClip      │                        │
│           (pre-event)  │    Recorder      │                        │
│                        │                  │                        │
│                        │  • 45s parallel  │                        │
│                        │  • Auto-complete │                        │
│                        └──────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
```

### How It Works

1. **FrameBuffer**: A ring buffer that holds the last 60 seconds of frames. When an arrival is detected, we can pull "pre-event" footage from before the detection.

2. **VisitRecorder**: Records the entire visit from arrival to departure. Produces `falcon_HHMMSS_visit.mp4`.

3. **ArrivalClipRecorder**: Records a 45-second arrival clip in parallel (15s before + 30s after arrival). Auto-completes and doesn't depend on departure.

4. **BufferClipManager**: Extracts the departure clip from the visit recording using ffmpeg (last 60s before + 30s after departure).

### The `.tmp` File Workflow

During arrival confirmation:

1. Detection triggers tentative arrival
2. Start recordings with `.tmp` suffix: `falcon_081530_arrival.mp4.tmp`
3. Run confirmation for 10 seconds
4. **If confirmed**: Rename to final: `falcon_081530_arrival.mp4`
5. **If cancelled**: Keep `.tmp` files for debugging, stop recordings

### Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `buffer_seconds` | 60 | Seconds of frames to keep in ring buffer |
| `clip_arrival_before` | 15 | Seconds before arrival for clip |
| `clip_arrival_after` | 30 | Seconds after arrival for clip |
| `clip_departure_before` | 30 | Seconds before departure for clip |
| `clip_departure_after` | 15 | Seconds after departure for clip |
| `clip_crf` | 23 | Video quality (lower = better, larger file) |

---

## The Full Detection Loop

Here's what happens every processed frame (in `BufferMonitor`):

```python
def process_frame(frame, timestamp):
    # 1. Push frame into ring buffer (keeps 60s of history)
    frame_buffer.add_frame(frame, timestamp)

    # 2. Write to active recordings (if any)
    if visit_recorder.is_recording:
        visit_recorder.write_frame(frame)
    if arrival_clip_recorder.is_recording():
        arrival_clip_recorder.write_frame(frame, timestamp)

    # 3. Run YOLO detection
    detections = detector.detect_birds(frame, timestamp=now)
    falcon_detected = len(detections) > 0

    # 4. Handle confirmation windows (arrival or startup)
    if arrival_pending:
        update_arrival_confirmation(falcon_detected)
    if startup_pending:
        update_startup_confirmation(falcon_detected)

    # 5. Update state machine → may generate events
    events = state_machine.update(falcon_detected, now)

    # 6. Handle events
    for event_type, event_time, metadata in events:
        if event_type == ARRIVED:
            start_confirmation_window()
            # Recordings start with .tmp suffix

        if event_type == DEPARTED:
            visit_path = visit_recorder.stop_recording(event_time)
            clip_manager.create_departure_clip(metadata)
            event_handler.handle_event(DEPARTED, event_time, metadata)
```

---

## Configuration Reference

### Full Configuration

```yaml
# ─────────────────────────────────────────────────────────────────────────────
# Stream & Detection
# ─────────────────────────────────────────────────────────────────────────────
video_source: "https://youtube.com/watch?v=..."
stream_name: "Harvard Falcon Cam"
timezone: "America/New_York"

detection_confidence: 0.4           # Daytime threshold
detection_confidence_ir: 0.2        # Night/IR threshold
frame_interval: 3                   # Process every Nth frame
model_path: models/yolov8n.pt
detect_any_animal: true

# ─────────────────────────────────────────────────────────────────────────────
# State Machine
# ─────────────────────────────────────────────────────────────────────────────
exit_timeout: 90                    # Seconds absent = departed
roosting_threshold: 1800            # 30 min = roosting notification
arrival_confirmation_seconds: 10    # Confirmation window
arrival_confirmation_ratio: 0.3     # 30% detection ratio required
notify_on_startup: false            # Notify if falcon present on startup

# ─────────────────────────────────────────────────────────────────────────────
# Clips
# ─────────────────────────────────────────────────────────────────────────────
clips_dir: clips
buffer_seconds: 60
clip_arrival_before: 15
clip_arrival_after: 30
clip_departure_before: 30
clip_departure_after: 15
clip_crf: 23
clip_fps: 30

# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────
telegram_enabled: true
telegram_channel: "@kanyo_harvard"
notification_cooldown_minutes: 5

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
log_level: INFO
log_file: logs/kanyo.log
```

---

## See Also

- [QUICKSTART.md](../QUICKSTART.md) — Get running in 10 minutes
- [adding-streams.md](adding-streams.md) — Multi-stream deployment
