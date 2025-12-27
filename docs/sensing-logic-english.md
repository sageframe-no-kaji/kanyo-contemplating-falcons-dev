# Kanyo Detection Logic — Plain English

## The Core Idea

**Problem**: Dumb systems send 100 alerts when a falcon sits still for 3 hours.

**Solution**: Wait and watch before deciding what's really happening.

---

## What the System Actually Detects

**Current capability**: Presence detection only.

The system answers ONE question: *"Is there a bird in the frame right now?"*

It does NOT currently detect:
- ❌ Preening
- ❌ Eating
- ❌ Wandering around
- ❌ Any specific behavior

This is presence/absence detection with smart debouncing to avoid alert spam.

---

## The Four States

The system is always in one of these four states:

| State | What it means |
|-------|---------------|
| **ABSENT** | No bird detected in frame. Waiting. |
| **VISITING** | Bird just showed up. Watching to see if it stays. |
| **ROOSTING** | Bird has been here a while. Settled in. Silent mode. |
| **BRIEF_ABSENCE** | Bird disappeared briefly during a roost. Probably just stepped out of frame. |

> ⚠️ **Note**: In the code, `BRIEF_ABSENCE` is currently named `ACTIVITY` — a misleading name. It does NOT mean the bird is "doing something interesting." It means the bird temporarily disappeared from view during roosting.

---

## The Rules

### When the nest is empty (ABSENT):

- **If bird detected** → Switch to VISITING, send "Falcon Arrived" alert with photo

---

### When bird just arrived (VISITING):

- **If bird still there after 30 minutes** → Switch to ROOSTING (no alert, just internal)
- **If bird gone for 2+ minutes** → Switch to ABSENT, send "Falcon Departed" alert, save clip
- **If bird keeps getting detected** → Stay in VISITING, keep watching

---

### When bird is settled in (ROOSTING):

- **If bird gone for 3+ minutes but less than 10** → Switch to BRIEF_ABSENCE (log it, no alert)
- **If bird gone for 10+ minutes** → Switch to ABSENT, send "Falcon Departed" alert with full visit summary, save clip
- **If bird keeps getting detected** → Stay in ROOSTING, stay silent

---

### When bird briefly disappeared during roost (BRIEF_ABSENCE):

- **If bird comes back** → Switch back to ROOSTING, log the gap
- **If bird stays gone for 10+ minutes total** → Switch to ABSENT, send "Falcon Departed" alert, save clip

---

## Startup Behavior

When the system first starts, it doesn't know if a bird is already there.

**For the first 30 seconds:**
- Look at every frame
- Don't send any alerts
- Just figure out what's going on

**After 30 seconds:**
- If bird was detected → Start in ROOSTING (assume it was already there)
- If no bird detected → Start in ABSENT

This prevents a false "Arrived!" alert when you restart the system with a bird already sitting there.

---

## The Timeouts (with defaults)

| Timeout | Default | What it controls |
|---------|---------|------------------|
| **exit_timeout** | 2 min | How long bird must be gone during a short visit before we say "departed" |
| **roosting_threshold** | 30 min | How long bird must stay before we consider it "roosting" |
| **roosting_exit_timeout** | 10 min | How long bird must be gone during roosting before we say "departed" |
| **activity_timeout** | 3 min | How long bird must be gone during roosting before we log it as a brief absence |

> ⚠️ **Naming note**: In config files, `activity_timeout` controls brief absences. The name is a legacy misnomer.

---

## What Gets Sent Where

| Event | Telegram Alert | Clip Created | Logged |
|-------|----------------|--------------|--------|
| Bird arrives | ✅ Photo + message | ✅ Arrival clip | ✅ |
| Bird departs | ✅ Photo + message + duration | ✅ Departure clip | ✅ |
| Switches to roosting | ❌ | ❌ | ✅ |
| Brief absence starts | ❌ | ❌ | ✅ |
| Brief absence ends | ❌ | ❌ | ✅ |
| **Bird does something interesting** | ❌ NOT IMPLEMENTED | ❌ | ❌ |

**Important**: Telegram alerts are sent **immediately** when events occur. Clip creation happens a few seconds later (needs "after" footage).
---

## The Buffer System

Instead of constantly recording video to disk, the system keeps the last 60 seconds of frames in memory.

**When bird arrives:**
- Grab the previous 15 seconds from memory (we already have it!)
- Start recording everything from now on

**When bird departs:**
- Stop recording
- Add 15 seconds after the last detection
- Save the whole visit as a video file
- Extract arrival clip and departure clip

This means we capture what happened *before* the bird arrived, without wasting disk space recording 24/7.

---

## Why Roosting Gets More Patience

A bird that just arrived might leave quickly. 2 minutes of no detection = probably gone.

A bird that's been there for an hour is probably just moving around, preening, or briefly out of frame. Give it 10 minutes before assuming it left.

This is why `roosting_exit_timeout` (10 min) is longer than `exit_timeout` (2 min).

---

## The Timing Rules That Must Be True

These relationships prevent impossible configurations:

1. **activity_timeout < roosting_exit_timeout**
   - Otherwise: Activity would immediately become departure

2. **exit_timeout < roosting_exit_timeout**
   - Otherwise: Roosting would have less patience than visiting (backwards)

3. **roosting_threshold > exit_timeout**
   - Otherwise: Bird would "depart" before it could ever reach roosting state

The system refuses to start if these rules are violated.

---

## Complete Visit Archiving

**Every single falcon visit is permanently archived**, regardless of length:

- **Event logs**: Date-organized JSON files (`events_2025-12-27.json`) with timestamps, durations, and activity counts
- **Video clips**: Date-organized folders (`clips/2025-12-27/`) with arrival, departure, and full visit videos
- **Thumbnails**: Snapshots of arrivals and departures for notifications

Short visits get one complete video. Long visits get separate arrival and departure clips. Nothing is ever lost.

---

## 🚧 MISSING FEATURE: Behavior Detection

### What We Wanted But Don't Have

The original vision was:
1. ✅ No alerts while bird is just sitting (roosting) — **IMPLEMENTED**
2. ✅ Debounce false detections — **IMPLEMENTED**
3. ❌ Alert when bird does something interesting (preening, eating, wandering) — **NOT IMPLEMENTED**

### Why It's Missing

The current YOLO model only answers: *"Is there a bird?"*

It cannot answer: *"What is the bird doing?"*

### Proposed Architecture for Behavior Detection

**Option 1: Motion-Based Activity (Simpler)**

Compare consecutive frames during roosting:
- Little movement → Still roosting, stay silent
- Significant movement → Something happening, send alert + clip

```
ROOSTING + motion detected → ACTIVE → alert + clip
ACTIVE + motion stops for 2 min → ROOSTING (silent)
```

Pros: Simple, no new models needed
Cons: Can't tell preening from wind-blown feathers

**Option 2: Pose/Action Classification (More Accurate)**

Add a second AI model trained to recognize falcon behaviors:
- Sitting still
- Preening
- Eating
- Wing stretching
- Looking around alertly
- Vocalizing (with audio)

```
ROOSTING + behavior_model(frame) == "eating" → alert + clip
ROOSTING + behavior_model(frame) == "preening" → alert + clip
ROOSTING + behavior_model(frame) == "sitting" → stay silent
```

Pros: Much more accurate, can filter by behavior type
Cons: Requires training data, more compute

**Option 3: Bounding Box Movement (Middle Ground)**

Track the bird's bounding box position/size over time:
- Box staying same size/position → Sitting still
- Box moving significantly → Wandering
- Box size changing → Wing movement

Pros: Uses existing YOLO output, no new model
Cons: Less precise than pose estimation

### Recommended Next Step

Start with **Option 3** (bounding box tracking):
1. During ROOSTING, track bbox center and size
2. If bbox moves > X pixels or changes size > Y%, trigger "activity"
3. Create clip, optionally send alert
4. Add cooldown to prevent spam

This gives us behavior detection without new models or training data.

---

## One-Sentence Summary

**Watch the stream, detect the bird, wait to be sure, alert on arrivals/departures, save video of everything.**

*(Behavior detection during roosting is a planned future feature.)*
