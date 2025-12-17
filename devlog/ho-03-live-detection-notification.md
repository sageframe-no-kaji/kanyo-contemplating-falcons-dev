# Ho 3: Real-Time Falcon Detection with Notifications

**Duration:** 2-3 hours
**Goal:** Connect to live falcon cam, detect arrivals in real-time, send notifications
**Deliverable:** Working script that monitors 24/7 and alerts you when falcon appears

---

## Why This Ho Matters

**Before Ho 3:** You can analyze pre-recorded video and find falcons after-the-fact

**After Ho 3:** You get notified THE MOMENT a falcon arrives at the nest

This is the transition from batch processing to real-time monitoring. You're building something that runs continuously and responds to events as they happen.

---

## Core Intentions

### 1. **Real-Time Stream Connection**

**Intention:** Continuously pull frames from YouTube live stream without downloading the entire thing

**What this means:**
- Not downloading 12-hour DVR window
- Not saving video to disk first
- Grabbing frames as they become available
- Handling stream disconnections/reconnections

**Why it's different from Ho 2:**
- Ho 2: Open file, process all frames, done
- Ho 3: Open stream, process forever, handle errors

---

### 2. **Continuous Detection Loop**

**Intention:** Run YOLOv8 detection on frames continuously without overwhelming the system

**What this means:**
- Process every Nth frame (not every frame - too slow)
- Keep detection running indefinitely
- Don't accumulate memory (process and discard frames)
- Balance detection frequency vs CPU usage

**Key question to answer:** How often do you need to check? Once per second? Twice? Five times?

---

### 3. **Event Detection with State**

**Intention:** Track whether falcon is "present" or "absent" and detect state changes

**What this means:**
- Maintain state: "Is falcon currently present?"
- Detect transitions: absent → present (ARRIVAL), present → absent (DEPARTURE)
- Use debouncing from Ho 2 (don't declare exit until N seconds of no detection)

**This is a state machine:**
```
ABSENT ──[detect bird]──> PRESENT
   ↑                          │
   └───[no bird for 30s]──────┘
```

---

### 4. **Notification with Cooldown**

**Intention:** Alert you when falcon arrives, but don't spam you if it keeps coming and going

**What this means:**
- Send notification on ARRIVAL event
- Don't send another notification for 1 hour (cooldown period)
- Use ntfy.sh (simple HTTP POST, no auth needed)
- Include timestamp and maybe confidence in notification

**Why cooldown matters:** Falcon might leave for 5 minutes and come back. You don't want 20 notifications per day.

---

### 5. **Graceful Operation**

**Intention:** Run for hours/days without crashing or leaking resources

**What this means:**
- Handle stream disconnections (retry automatically)
- Handle detection failures (log and continue)
- Handle keyboard interrupt cleanly (Ctrl+C to stop)
- Log everything so you can debug issues

**This is about robustness:** System should recover from errors and keep running.

---

## Major Components & Concepts

### Component 1: Stream Capture (Already exists!)

**You already have:** `StreamCapture` class from Ho 2

**What it does:**
- Connects to YouTube stream
- Yields frames continuously
- Handles reconnection automatically

**What you'll use:**
```python
capture = StreamCapture(stream_url)
for frame in capture.frames(skip=30):
    # Process this frame
    pass
```

**Concept:** Infinite iterator pattern - frames keep coming until you stop the loop

---

### Component 2: Falcon Detector (Already exists!)

**You already have:** `FalconDetector` class from Ho 2

**What it does:**
- Runs YOLOv8 on a frame
- Returns list of detections
- Filters for animals (birds, cats, etc.)

**What you'll use:**
```python
detector = FalconDetector(confidence_threshold=0.5)
detections = detector.detect_birds(frame)
falcon_present = len(detections) > 0
```

**Concept:** Reusing existing detection logic - no need to rewrite

---

### Component 3: Visit Tracker (New - needs building)

**Intention:** Track current visit state and detect enter/exit events

**What it needs to do:**
- Remember: "Is falcon currently present?"
- Remember: "When did we last see falcon?"
- Decide: "Has falcon just arrived?" (absent → present)
- Decide: "Has falcon just left?" (present → absent for 30+ seconds)

**State to maintain:**
- `current_visit: FalconVisit | None` - Active visit or None
- `last_detection_time: datetime | None` - When we last saw falcon

**Concept:** State machine with timeout - exit only declared after debounce period

---

### Component 4: Notification Sender (New - needs building)

**Intention:** Send push notification via ntfy.sh with cooldown

**What it needs to do:**
- POST to ntfy.sh when falcon enters
- Include title, message, timestamp
- Track last notification time
- Skip notification if < 1 hour since last one

**API pattern:**
```python
import requests

requests.post(
    "https://ntfy.sh/your-topic-name",
    data="🦅 Falcon arrived at 2:30 PM!",
    headers={"Title": "Falcon Alert"}
)
```

**State to maintain:**
- `last_notification_time: datetime | None`

**Concept:** Simple HTTP POST with rate limiting

---

### Component 5: Main Loop Orchestration (New - needs building)

**Intention:** Tie everything together in a continuous loop

**Pseudocode flow:**
```
1. Connect to stream
2. Load detector
3. Initialize visit tracker
4. FOR EACH FRAME from stream:
   a. Run detection
   b. Update visit state
   c. If ARRIVAL detected:
      - Send notification (if cooldown allows)
      - Log event
   d. If DEPARTURE detected:
      - Log event
   e. Sleep briefly to prevent CPU spin
5. Handle Ctrl+C gracefully
```

**Concept:** Main event loop pattern - orchestrate components in infinite loop

---

## Configuration Values to Add

```yaml
# Real-time monitoring
stream_url: "https://www.youtube.com/watch?v=glczTFRRAK4"
frame_skip: 30                # Process every 30th frame
visit_timeout: 30             # Seconds of no detection = visit ended

# Notifications
ntfy_topic: "kanyo-falcon"    # Your ntfy.sh topic name
notification_cooldown: 3600   # Seconds (1 hour)
notification_enabled: true    # Easy on/off switch
```

---

## Key Decisions to Make

### Decision 1: Frame Processing Rate

**Question:** How often should we check for falcons?

**Options:**
- Every frame (30 fps) = too slow, CPU overload
- Every 15th frame (2 fps) = good balance
- Every 30th frame (1 fps) = fast, might miss brief appearances

**Recommendation:** Start with 30 (1 fps), tune if needed

---

### Decision 2: Visit Timeout

**Question:** How long should falcon be gone before we declare "visit ended"?

**Options:**
- 10 seconds = too short, creates spam from brief occlusions
- 30 seconds = balanced (from Ho 2 testing)
- 60 seconds = might miss quick visits

**Recommendation:** Use 30 seconds (proven in Ho 2)

---

### Decision 3: Notification Content

**Question:** What should the notification say?

**Options:**
- Minimal: "Falcon detected"
- Detailed: "Falcon arrived at 2:30 PM (confidence: 87%)"
- Link: Include YouTube stream link

**Recommendation:** Title + timestamp + link to watch live

---

### Decision 4: Error Handling Strategy

**Question:** What happens when something breaks?

**Stream disconnects:**
- Retry automatically (StreamCapture handles this)
- Log reconnection attempts

**Detection fails:**
- Log error
- Skip frame and continue
- Don't crash entire system

**Keyboard interrupt:**
- Clean shutdown
- Save any ongoing visit
- Close stream gracefully

---

## Success Criteria

**At the end of Ho 3, you should be able to:**

✅ Run `python realtime_monitor.py` on your Mac
✅ See it connect to falcon cam
✅ Watch log messages as it processes frames
✅ Get a notification on your phone when falcon detected
✅ Not get spammed (cooldown works)
✅ Stop it cleanly with Ctrl+C
✅ Leave it running for an hour without crashes

**You should understand:**
- How infinite loops work for monitoring
- How state machines track presence/absence
- How to send HTTP notifications
- How to handle errors gracefully
- How frame skipping affects detection

---

## What You're NOT Doing (Save for Later)

❌ **Saving clips from live stream** - Ho 2 clips were from files, live stream clip extraction is complex (need buffer)
❌ **Docker deployment** - Ho 4
❌ **Running on bird box** - Ho 4
❌ **Web interface** - Future Ho
❌ **Historical archive** - Future Ho

---

## Testing Strategy

**Phase 1: Sanity check (5 minutes)**
- Connect to stream
- Print "Frame received" for each frame
- Verify it doesn't crash

**Phase 2: Detection test (10 minutes)**
- Add detection
- Print when bird detected
- Verify confidence threshold works

**Phase 3: State machine test (15 minutes)**
- Add visit tracking
- Print ENTER/EXIT events
- Verify debouncing works

**Phase 4: Notification test (10 minutes)**
- Add ntfy.sh calls
- Send test notification
- Verify cooldown works

**Phase 5: Endurance test (60+ minutes)**
- Let it run for an hour
- Check logs for errors
- Verify no memory leaks

---

## File Structure

**New file:**
- `src/kanyo/detection/realtime_monitor.py` - Main monitoring loop

**Modified files:**
- `config.yaml` - Add notification settings
- `requirements.txt` - Add `requests` for ntfy.sh

**Script to run:**
- `scripts/start_monitoring.py` - Entry point with config loading

---

## Major Concepts You'll Learn

1. **Infinite loops for monitoring** - How to structure code that runs forever
2. **State machines** - Tracking presence/absence transitions
3. **Debouncing** - Ignoring brief gaps in detection
4. **Rate limiting** - Cooldown periods for notifications
5. **Error recovery** - Handling failures without crashing
6. **Graceful shutdown** - Cleaning up resources on exit

---

## Potential Gotchas

**Stream lag:** Live stream might be 10-30 seconds behind real-time
**Frame drops:** Network hiccups cause missed frames
**CPU usage:** Detection is intensive, need to balance frequency
**Memory leaks:** Must release frames after processing
**Notification failures:** ntfy.sh might be down, need fallback

---

## When You're Stuck

**Ask yourself:**
1. Is the stream connecting? (Check logs)
2. Are frames being received? (Add print statements)
3. Is detection running? (Log detection results)
4. Is state updating correctly? (Log state changes)
5. Are notifications sending? (Check ntfy.sh app)

**Debug incrementally:** Add one piece at a time, verify it works, then add next piece.

---

## End State

**You'll have a script that:**
- Monitors falcon cam 24/7
- Detects when falcon arrives
- Sends you a push notification
- Logs all events
- Handles errors gracefully
- Can run on your Mac while you're away

**You'll understand:**
- Real-time monitoring patterns
- State machines for event detection
- HTTP notification APIs
- Graceful error handling

---

**Ready to start Ho 3?** 🦅
