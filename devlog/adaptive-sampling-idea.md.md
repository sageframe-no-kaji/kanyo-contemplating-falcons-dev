# Kanyō Camera State Machine & Adaptive Sampling Architecture

## Design Specification and Recommendations

---

# Core Principle

Kanyō is not polling cameras at fixed intervals.
Kanyō is allocating attention dynamically based on evidence.

Each camera moves through a **state machine** that reflects its real-world activity, and sampling frequency adjusts accordingly.

This improves:

- detection probability
- compute efficiency
- scalability
- observability
- user understanding of camera behavior

---

# Goals

1. Maximize detection probability
2. Minimize unnecessary YOLO inference
3. Adapt automatically over time
4. Reflect real biological patterns
5. Expose state transparently in the interface
6. Support both immediate responsiveness and long-term learning

---

# Core Camera States

These states reflect the camera’s current short-term activity level.

```text
ACTIVE      — Falcon present recently
WARM        — Falcon present recently but not currently
IDLE        — Occasional detections
QUIET       — Rare detections
DORMANT     — Essentially inactive
OFFLINE     — Camera unavailable


⸻

State Definitions

ACTIVE

Falcon detected very recently.

Typical conditions:
	•	detection within last 30 seconds
	•	repeated detections ongoing

Sampling interval:

interval_sec: 1–2

Purpose:

Maximize temporal resolution during presence.

⸻

WARM

Falcon detected recently but not continuously.

Typical conditions:
	•	detection within last 10 minutes

Sampling interval:

interval_sec: 3–5

Purpose:

High readiness for return.

⸻

IDLE

Falcon detected occasionally.

Typical conditions:
	•	detection within last 24 hours

Sampling interval:

interval_sec: 10–30

Purpose:

Maintain awareness without excessive compute.

⸻

QUIET

Rare detections.

Typical conditions:
	•	detection within last 7 days

Sampling interval:

interval_sec: 30–120

Purpose:

Low-level monitoring.

⸻

DORMANT

Extremely rare or seasonal activity.

Typical conditions:
	•	no detections within last 30 days

Sampling interval:

interval_sec: 120–300

Purpose:

Long-term monitoring at minimal cost.

⸻

OFFLINE

Camera unreachable.

Sampling interval:

interval_sec: 300–600

Purpose:

Periodic recovery attempts.

⸻

State Transition Logic

State transitions are fully automatic.

Example logic:

if detection_within(30 seconds):
    state = ACTIVE

elif detection_within(10 minutes):
    state = WARM

elif detection_within(24 hours):
    state = IDLE

elif detection_within(7 days):
    state = QUIET

elif detection_within(30 days):
    state = DORMANT

else:
    state = DORMANT


⸻

Temporal Activity Model (Multi-Timescale Tracking)

Each camera tracks activity across multiple rolling windows.

These are separate from state and provide statistical context.

activity_metrics:
  last_detection_at: timestamp

  detections:
    last_hour: int
    last_day: int
    last_week: int
    last_month: int
    lifetime: int

  detection_rate:
    hourly_avg: float
    daily_avg: float
    weekly_avg: float
    monthly_avg: float

This allows Kanyō to understand:
	•	short-term activity
	•	seasonal patterns
	•	long-term reliability

⸻

Camera Classification Metadata

Each camera should include static classification fields.

classification:
  camera_type: perch | nest_exterior | nest_interior | unknown

  habitat_type:
    building_ledge
    bridge
    nest_box
    cliff
    tower
    mixed

  expected_activity:
    high
    medium
    low
    seasonal

  reliability:
    high
    medium
    low

This allows smarter default scheduling.

⸻

Adaptive Sampling Algorithm

Sampling interval is derived from state.

Example:

STATE_INTERVALS = {
    "ACTIVE": 2,
    "WARM": 5,
    "IDLE": 20,
    "QUIET": 60,
    "DORMANT": 300,
    "OFFLINE": 600,
}

Scheduler loop:

for camera in cameras:

    state = compute_state(camera)

    interval = STATE_INTERVALS[state]

    if time_since(camera.last_sample) >= interval:
        process_frame(camera)


⸻

Optional Stage 2 Optimization: Motion Gating

Before running YOLO, perform cheap image difference detection.

if image_changed_significantly:
    run_yolo()
else:
    skip()

This can reduce YOLO usage by 80–98%.

⸻

Camera Database Schema Recommendation (SQLite)

CREATE TABLE cameras (
    id TEXT PRIMARY KEY,

    name TEXT,
    stream_url TEXT,

    camera_type TEXT,
    habitat_type TEXT,
    expected_activity TEXT,

    state TEXT,

    last_detection_at DATETIME,
    last_sample_at DATETIME,

    detections_last_hour INTEGER,
    detections_last_day INTEGER,
    detections_last_week INTEGER,
    detections_last_month INTEGER,
    detections_lifetime INTEGER,

    created_at DATETIME,
    updated_at DATETIME
);


⸻

Scheduler Architecture

Scheduler Loop
    ↓
Evaluate state
    ↓
Determine sampling interval
    ↓
Capture frame
    ↓
Motion detection
    ↓
YOLO inference (if needed)
    ↓
Update detection metrics
    ↓
Update camera state


⸻

Interface Recommendations

Expose state directly in Kanyō UI.

Example display:

Harvard Science Center
State: ACTIVE
Detections today: 142
Last detection: 12 seconds ago
Activity level: Very High

Example quiet camera:

Random Nest Box
State: DORMANT
Detections today: 0
Last detection: 17 days ago
Activity level: Very Low

This dramatically improves user understanding.

⸻

Expected Compute Savings

Typical improvement:

Naive fixed polling:
100 cameras × 2 sec interval = 50 inferences/sec

Adaptive polling:
~5–10 inferences/sec

Reduction: 80–90%


⸻

Recommended Implementation Phases

Phase 1 (Immediate)
	•	camera_type field
	•	state field
	•	last_detection_at tracking
	•	adaptive intervals

Phase 2
	•	rolling activity metrics
	•	multi-timescale tracking

Phase 3
	•	motion gating
	•	predictive scheduling

Phase 4
	•	seasonal learning
	•	per-camera behavioral modeling

⸻

Conceptual Model

Kanyō behaves like a distributed observer.

It watches active places closely.

It glances occasionally at quiet places.

It never wastes attention.

⸻

Summary

This state machine architecture allows Kanyō to:
	•	scale to hundreds or thousands of cameras
	•	maximize detections
	•	minimize compute
	•	reflect real falcon behavior
	•	expose meaningful activity information to users
	•	continuously improve over time

This is the correct architectural foundation.

```
