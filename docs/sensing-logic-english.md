Here's the updated `sensing-logic-english.md`:

```markdown
# Kanyo Detection Logic — Plain English

## The Core Idea

**Problem**: Dumb systems send 100 alerts when a falcon sits still for 3 hours.

**Solution**: Wait and watch before deciding what's really happening.

---

## What the System Actually Detects

**Current capability**: Presence detection only.

The system answers ONE question: *"Is there a bird in the frame right now?"*

It does NOT detect:
- ❌ Preening
- ❌ Eating
- ❌ Wandering around
- ❌ Any specific behavior

This is presence/absence detection with smart debouncing to avoid alert spam.

---

## The Three States

The system is always in one of these three states:

| State | What it means |
|-------|---------------|
| **ABSENT** | No bird detected in frame. Waiting. |
| **VISITING** | Bird just showed up. Watching to see if it stays. |
| **ROOSTING** | Bird has been here 30+ minutes. Settled in. |

That's it. Three states. Simple.

---

## The Rules

### When the nest is empty (ABSENT):

- **If bird detected** → Switch to VISITING, send "Falcon Arrived" alert with photo

---

### When bird just arrived (VISITING):

- **If bird still there after 30 minutes** → Switch to ROOSTING, send "Settled in" notification
- **If bird gone for 90 seconds** → Switch to ABSENT, send "Falcon Departed" alert, save clip
- **If bird keeps getting detected** → Stay in VISITING, keep watching

---

### When bird is settled in (ROOSTING):

- **If bird gone for 90 seconds** → Switch to ABSENT, send "Falcon Departed" alert with full visit summary, save clip
- **If bird keeps getting detected** → Stay in ROOSTING, stay silent

---

## The Debounce (Rolling Timeout)

This is the key to eliminating false alerts.

Every frame where we DON'T see the bird, we start counting. Every frame where we DO see the bird, the counter resets to zero.

```
Frame 1: Bird detected     → counter = 0
Frame 2: Bird detected     → counter = 0
Frame 3: No bird           → counter starts (1 second...)
Frame 4: No bird           → counter continues (2 seconds...)
Frame 5: Bird detected     → counter RESETS to 0
Frame 6: No bird           → counter starts again (1 second...)
...
Frame N: No bird           → counter hits 90 seconds → DEPARTED
```

The bird must be gone for a **continuous** 90 seconds. If it flickers back into view at 89 seconds, the counter resets and we start over.

This is why YOLO detection flicker doesn't cause false departures.

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

## The Timeouts

Only two timeouts to configure:

| Timeout | Default | What it controls |
|---------|---------|------------------|
| **exit_timeout** | 90 seconds | How long bird must be continuously gone before we say "departed" |
| **roosting_threshold** | 30 minutes | How long bird must stay before we consider it "roosting" |

That's it. Same exit timeout for VISITING and ROOSTING. Simple.

---

## What Gets Sent Where

| Event | Telegram Alert | Clip Created | Logged |
|-------|----------------|--------------|--------|
| Bird arrives | ✅ Photo + message | ✅ Arrival clip (45s) | ✅ |
| Bird departs | ✅ Photo + message + duration | ✅ Departure clip (90s) | ✅ |
| Switches to roosting | ✅ "Settled in" message | ❌ | ✅ |

**Timing**: Telegram alerts are sent **immediately** when events occur. Arrival clips complete 45 seconds after arrival. Departure clips are extracted after the visit file closes.

---

## The Buffer System

Instead of constantly recording video to disk, the system keeps the last 60 seconds of frames in memory (JPEG compressed).

**When bird arrives:**
1. Grab the previous 15 seconds from memory (we already have it!)
2. Start recording the full visit
3. Start a parallel 45-second arrival clip (15s before + 30s after)

**When bird departs:**
1. Stop recording
2. Save the whole visit as a video file
3. Extract departure clip from the visit file (60s before + 30s after last detection)

This means we capture what happened *before* the bird arrived, without wasting disk space recording 24/7.

---

## Clips Created

| Clip | Duration | When Created | Contents |
|------|----------|--------------|----------|
| **Arrival** | 45s | During visit (parallel recording) | 15s before + 30s after arrival |
| **Departure** | 90s | After visit ends | 60s before + 30s after last detection |
| **Full Visit** | Variable | After visit ends | Everything from arrival to departure |

**Key insight**: The departure clip uses the **last detection time**, not the end of the file. If the bird left at 12:05 but the system didn't declare departure until 12:06:30 (after the 90s timeout), the clip shows 12:04 to 12:05:30 — the actual departure, not empty nest.

---

## Complete Visit Archiving

**Every single falcon visit is permanently archived**, regardless of length:

- **Event logs**: Date-organized JSON files (`events_2025-12-28.json`) with timestamps and durations
- **Video clips**: Date-organized folders (`clips/2025-12-28/`) with arrival, departure, and full visit videos
- **Thumbnails**: Snapshots of arrivals and departures for notifications

Short visits get one complete video. Long visits get separate arrival and departure clips. Nothing is ever lost.

---

## Configuration

```yaml
# Detection
video_source: "https://youtube.com/..."
detection_confidence: 0.35    # Lower = more sensitive, more false positives
frame_interval: 3             # Process every Nth frame

# State machine
exit_timeout: 90              # Seconds gone = departed (all states)
roosting_threshold: 1800      # 30 min = roosting notification

# Clips
clip_arrival_before: 15       # Seconds before arrival in clip
clip_arrival_after: 30        # Seconds after arrival in clip
clip_departure_before: 60     # Seconds before departure in clip
clip_departure_after: 30      # Seconds after departure in clip

# Timezone (for logs and filenames)
timezone: "-05:00"
```

---

## One-Sentence Summary

**Watch the stream, detect the bird, wait 90 seconds to be sure it's gone, alert on arrivals/departures, save video of everything.**
```
