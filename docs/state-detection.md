# Falcon State Machine: Intelligent Activity Detection

## Overview

The falcon detection system uses a state machine to intelligently track falcon presence and behavior, eliminating false enter/exit cycles during roosting while maintaining accurate activity reporting.

### The Problem

Traditional detection systems treat every absence as a potential departure, leading to:
- 🚫 Dozens of false "ENTERED" and "EXITED" alerts during roosting
- 🚫 Fragmented visit records (one long roost becomes many short visits)
- 🚫 Notification spam and wasted clip storage
- 🚫 Inaccurate behavioral analytics

### The Solution

A **state machine** that understands falcon behavior patterns:
- ✅ Distinguishes between short visits and long roosting periods
- ✅ Tracks brief activity during roosting as normal behavior
- ✅ Only reports true arrivals and departures
- ✅ Maintains complete behavioral records with activity periods

---

## State Machine Architecture

### States

The system tracks four distinct behavioral states:

```
┌─────────┐
│ ABSENT  │ ◄─── No falcon detected
└─────────┘
     │ ▲
     │ │ Detection starts/ends
     ▼ │
┌──────────┐
│ VISITING │ ◄─── Short-term presence (< 30 min)
└──────────┘
     │ ▲
     │ │ 30 min threshold / departure
     ▼ │
┌───────────┐
│ ROOSTING  │ ◄─── Long-term presence (> 30 min)
└───────────┘
     │ ▲
     │ │ Brief absence / return
     ▼ │
┌──────────┐
│ ACTIVITY │ ◄─── Brief movement during roost
└──────────┘
```

#### 1. **ABSENT**
- No falcon detected
- Initial state on startup
- Waiting for first detection

#### 2. **VISITING**
- Falcon present for < 30 minutes
- Typical short visit behavior
- Exit timeout: 5 minutes (normal detection gaps)

#### 3. **ROOSTING**
- Falcon present for > 30 minutes
- Long-term roosting behavior
- Exit timeout: 10 minutes (allows brief absences)

#### 4. **ACTIVITY**
- Brief absence (1-3 min) during roosting
- Represents normal movement (preening, hunting, etc.)
- Automatically returns to ROOSTING when detection resumes

### State Transitions

#### ABSENT → VISITING
**Trigger:** Falcon detected after absence
**Event:** `ARRIVED`
**Actions:**
- Log arrival time
- Send arrival notification
- Capture arrival thumbnail
- Start visit timer

#### VISITING → ROOSTING
**Trigger:** Continuous presence for 30 minutes
**Event:** `ROOSTING`
**Actions:**
- Log transition to roosting state
- NO notification (internal event)
- Continue monitoring with extended timeout

#### VISITING → ABSENT
**Trigger:** No detection for 5 minutes during visit
**Event:** `DEPARTED`
**Actions:**
- Log departure with visit duration
- Send departure notification
- Capture departure thumbnail
- Create visit clip
- Reset state

#### ROOSTING → ACTIVITY
**Trigger:** Brief absence (> 3 min) during roost
**Event:** `ACTIVITY_START`
**Actions:**
- Log activity period start
- Optional notification (usually disabled)
- Track activity duration

#### ACTIVITY → ROOSTING
**Trigger:** Detection resumes during activity
**Event:** `ACTIVITY_END`
**Actions:**
- Log activity period end
- Record activity duration
- Resume roosting monitoring

#### ROOSTING → ABSENT
**Trigger:** No detection for 10 minutes during roost
**Event:** `DEPARTED`
**Actions:**
- Log departure with total visit duration and activity count
- Send departure notification
- Capture departure thumbnail
- Create complete visit clip
- Reset state

---

## Configuration Parameters

### Timing Thresholds

All timing parameters are configured in `config.yaml`:

```yaml
# State machine configuration for roosting detection
exit_timeout: 300              # 5 min - mark departed if absent this long during visit
roosting_threshold: 1800       # 30 min - transition to roosting state
roosting_exit_timeout: 600     # 10 min - must be absent this long when roosting
activity_timeout: 180          # 3 min - brief absence during roost is activity
activity_notification: false   # Send notifications for activity events?
```

#### `exit_timeout` (default: 300 seconds / 5 minutes)
**Purpose:** Departure threshold during VISITING state

**Tuning Guidelines:**
- **Too short:** False departures during normal detection gaps
- **Too long:** Delayed departure reporting
- **Recommended:** 300-600 seconds (5-10 minutes)

**Consider shorter (180-300s) if:**
- High-quality stream with consistent detection
- Falcons typically make short visits
- Want immediate departure alerts

**Consider longer (600-900s) if:**
- Unreliable stream with detection gaps
- Camera angle causes occlusion
- False departures are a problem

#### `roosting_threshold` (default: 1800 seconds / 30 minutes)
**Purpose:** Time before transitioning from VISITING to ROOSTING

**Tuning Guidelines:**
- **Too short:** Normal visits incorrectly treated as roosting
- **Too long:** Roosting behavior not detected, excessive false exits
- **Recommended:** 1800-3600 seconds (30-60 minutes)

**Consider shorter (1200-1800s) if:**
- Falcons roost frequently at this location
- Want earlier detection of roosting behavior
- Seeing many false exits during known roosting periods

**Consider longer (3600-5400s) if:**
- Distinguishing roosting from feeding/nesting
- Want to reserve roosting state for overnight periods
- False roosting transitions are occurring

#### `roosting_exit_timeout` (default: 600 seconds / 10 minutes)
**Purpose:** Departure threshold during ROOSTING state

**Tuning Guidelines:**
- **Too short:** Brief hunting trips treated as departures
- **Too long:** Actual departures delayed
- **Recommended:** 600-900 seconds (10-15 minutes)

**Must be > `exit_timeout`** to prevent shorter timeout than visiting state.

**Consider shorter (480-600s) if:**
- Falcons rarely leave during roosting
- Want faster departure detection
- Stream quality is excellent

**Consider longer (900-1800s) if:**
- Falcons make brief hunting trips during roost
- Seeing false departures during known roosting
- Stream has periodic detection failures

#### `activity_timeout` (default: 180 seconds / 3 minutes)
**Purpose:** Maximum absence duration to count as activity vs departure

**Tuning Guidelines:**
- **Too short:** Normal movement treated as departure
- **Too long:** Brief departures treated as activity
- **Recommended:** 120-300 seconds (2-5 minutes)

**Must be < `roosting_exit_timeout`** to allow detection of activity before departure.

**Consider shorter (120-180s) if:**
- Falcons are mostly stationary when roosting
- Want to distinguish micro-movements only
- Seeing false activity periods

**Consider longer (300-600s) if:**
- Falcons move frequently during roost
- Brief hunting trips are normal
- Want more lenient activity detection

#### `activity_notification` (default: false)
**Purpose:** Send notifications for activity events

**Recommendations:**
- **Keep false** for most deployments (reduces notification spam)
- **Set true** if activity periods are scientifically significant
- **Enable for debugging** to understand falcon behavior patterns

---

## Optimizing for Accurate Activity Reporting

### Goal: Ensure Accurate, Meaningful Activity Tracking

The state machine is designed to report **actual behavioral events**, not every detection fluctuation.

### Best Practices

#### 1. **Tune for Your Environment**

Each camera location has unique characteristics:
- **Stream quality:** Bandwidth, resolution, frame rate
- **Camera angle:** Occlusion by nest structure, branches
- **Lighting conditions:** Day/night transitions, shadows
- **Falcon behavior:** Typical visit duration, roosting patterns

**Process:**
1. Start with default values
2. Monitor logs for false positives/negatives
3. Adjust one parameter at a time
4. Test for 24-48 hours before next adjustment

#### 2. **Balance Sensitivity vs. Stability**

**High Sensitivity (shorter timeouts):**
- ✅ Faster detection of state changes
- ✅ More granular activity tracking
- ❌ More false positives during detection gaps
- ❌ Fragmented visit records

**High Stability (longer timeouts):**
- ✅ Fewer false state transitions
- ✅ Clean, consolidated visit records
- ❌ Delayed detection of departures
- ❌ May miss brief activity periods

**Recommended:** Start stable, increase sensitivity gradually

#### 3. **Monitor Detection Quality**

Enable DEBUG logging to see confidence scores:

```yaml
log_level: "DEBUG"
```

Check logs for patterns:
```
2025-12-19 02:48:15 | DEBUG | Falcon detected: confidence=0.876
2025-12-19 02:48:18 | DEBUG | Falcon detected: confidence=0.882
2025-12-19 02:48:21 | DEBUG | No falcon detected (checked 3 detections)
```

**Signs of detection issues:**
- Frequent "No falcon detected" during known presence
- Confidence scores < 0.6 consistently
- Alternating detected/not-detected every few seconds

**Solutions:**
- Improve lighting at nest site
- Adjust camera angle to reduce occlusion
- Lower `detection_confidence` threshold (if false positives aren't a problem)
- Increase timeout values to handle gaps

#### 4. **Understand Timing Relationships**

Critical constraints:
```
activity_timeout < roosting_exit_timeout
exit_timeout < roosting_exit_timeout
```

**Recommended ratios:**
```yaml
exit_timeout: 300              # 5 min (base unit)
activity_timeout: 180          # 3 min (60% of exit_timeout)
roosting_exit_timeout: 600     # 10 min (2x exit_timeout)
roosting_threshold: 1800       # 30 min (6x exit_timeout)
```

These ratios ensure:
- Activity detection before departure during roost
- Clear distinction between visiting and roosting
- Logical progression through states

#### 5. **Validate with Ground Truth**

**Method 1: Manual Observation**
- Watch live stream for 1-2 hours
- Note actual arrivals, departures, activity
- Compare with system logs
- Calculate accuracy metrics

**Method 2: Historical Analysis**
```python
# Analyze events.json for patterns
import json
from datetime import datetime, timedelta

events = json.load(open('clips/2025-12-19/events_2025-12-19.json'))

# Check for suspicious patterns
for visit in events:
    duration = visit['end_time'] - visit['start_time']

    # Flag suspiciously short visits during roosting hours
    if duration < 300 and is_roosting_time(visit['start_time']):
        print(f"Possible false departure: {visit}")

    # Flag long visits without activity
    if duration > 7200 and visit['activity_periods'] == 0:
        print(f"Possible missed activity: {visit}")
```

**Method 3: Compare Days**
- Run with different configs on different days
- Compare total visit time, visit count, activity periods
- Choose config that best matches observed behavior

---

## Troubleshooting Common Issues

### Issue: Too Many False "DEPARTED" Events

**Symptoms:**
- Multiple short visits (< 5 min) during known roosting
- Departure notifications every 10-15 minutes
- Fragmented event records

**Diagnosis:**
- Detection gaps exceeding timeout thresholds
- Timeout values too short for this environment

**Solutions:**
1. Increase `exit_timeout` to 600-900 seconds
2. Increase `roosting_exit_timeout` to 900-1200 seconds
3. Lower `detection_confidence` if false positives aren't a problem
4. Check stream quality and camera positioning

### Issue: Roosting State Never Triggered

**Symptoms:**
- Long visits (> 1 hour) never transition to ROOSTING
- All periods treated as VISITING
- Many false departures during long stays

**Diagnosis:**
- `roosting_threshold` too high
- Falcon departing before threshold reached
- Detection gaps causing state resets

**Solutions:**
1. Reduce `roosting_threshold` to 1200-1800 seconds (20-30 min)
2. Increase `exit_timeout` to prevent premature departures
3. Review logs for actual visit durations

### Issue: Activity Periods Not Detected

**Symptoms:**
- No ACTIVITY_START/ACTIVITY_END events in logs
- State goes directly from ROOSTING to ABSENT
- Brief absences during roost not tracked

**Diagnosis:**
- `activity_timeout` too short
- Brief absences not exceeding threshold
- State already in ABSENT before activity could be detected

**Solutions:**
1. Increase `activity_timeout` to 240-360 seconds
2. Ensure `activity_timeout` < `roosting_exit_timeout`
3. Enable DEBUG logging to see detection patterns

### Issue: Too Many Activity Alerts

**Symptoms:**
- Constant ACTIVITY_START/ACTIVITY_END events
- Falcon never stays in ROOSTING state
- Activity periods every few minutes

**Diagnosis:**
- `activity_timeout` too long
- Normal roosting behavior triggering activity detection
- Detection quality issues causing false absences

**Solutions:**
1. Reduce `activity_timeout` to 120-180 seconds
2. Review detection confidence scores (enable DEBUG)
3. Disable `activity_notification` to reduce alert spam
4. Consider if this is normal behavior (very active falcon)

### Issue: Delayed Departure Detection

**Symptoms:**
- Falcon visibly leaves but no departure event for 10+ minutes
- Departure times don't match observed departures
- Missing critical departure events

**Diagnosis:**
- Timeout values too long for this use case
- State machine correctly waiting for confirmation

**Solutions:**
1. Reduce `exit_timeout` to 180-300 seconds (if stream quality allows)
2. Reduce `roosting_exit_timeout` to 480-600 seconds
3. Accept trade-off: faster detection = more false positives
4. Consider if immediate departure detection is critical for your goals

---

## Event Log Examples

### Example 1: Short Visit

```
2025-12-19 14:23:15 | INFO | 🦅 FALCON ARRIVED at 02:23:15 PM
2025-12-19 14:35:42 | INFO | 🦅 FALCON DEPARTED at 02:30:42 PM (12m 27s visit)
```

**Analysis:**
- Visit duration: 12m 27s
- Did not reach roosting threshold (30 min)
- Stayed in VISITING state entire time
- Single arrival/departure pair

### Example 2: Roosting with Activity

```
2025-12-19 08:05:12 | INFO | 🦅 FALCON ARRIVED at 08:05:12 AM
2025-12-19 08:35:12 | INFO | 🏠 Falcon transitioned to ROOSTING state (30m)
2025-12-19 10:22:45 | DEBUG | 🦅 Falcon activity detected during roost
2025-12-19 10:26:33 | DEBUG | 🦅 Falcon settled after activity (3m 48s)
2025-12-19 11:08:17 | DEBUG | 🦅 Falcon activity detected during roost
2025-12-19 11:10:02 | DEBUG | 🦅 Falcon settled after activity (1m 45s)
2025-12-19 13:42:09 | INFO | 🦅 FALCON DEPARTED at 01:32:09 PM (5h 27m visit, 2 activity periods)
```

**Analysis:**
- Total visit: 5h 27m
- Transitioned to roosting after 30 min
- Two activity periods during roost (3m 48s, 1m 45s)
- Departure after 10 min absence
- Clean record of entire roosting session

### Example 3: Overnight Roost

```
2025-12-18 18:15:33 | INFO | 🦅 FALCON ARRIVED at 06:15:33 PM
2025-12-18 18:45:33 | INFO | 🏠 Falcon transitioned to ROOSTING state (30m)
[Many activity periods throughout night - abbreviated]
2025-12-19 05:48:12 | INFO | 🦅 FALCON DEPARTED at 05:38:12 AM (11h 23m visit, 8 activity periods)
```

**Analysis:**
- Overnight roosting session: 11h 23m
- 8 tracked activity periods (preening, shifting position)
- Single arrival/departure pair despite 11+ hours
- State machine correctly identified roosting behavior

---

## Performance Considerations

### Memory Usage

**Per-visit tracking:**
- Visit start/end timestamps: ~40 bytes
- Activity period list: ~20 bytes per period
- State machine overhead: ~200 bytes

**Typical usage:**
- 24-hour monitoring: 5-20 visits
- Memory: < 10 KB for state tracking

### CPU Impact

**State machine overhead:**
- Per-frame check: < 0.1ms
- Event generation: < 1ms per event
- Total impact: < 0.1% of detection CPU time

**Conclusion:** Negligible performance impact

### Log File Growth

**With INFO logging:**
- ~50 KB per day (arrival/departure only)

**With DEBUG logging:**
- ~5 MB per day (includes confidence scores)

**Recommendation:**
- Use INFO for production
- Enable DEBUG for tuning/debugging only
- Rotate logs weekly

---

## Advanced Configuration

### Multiple Cameras

Run separate instances with different configs:

```bash
# Camera 1: Active nest with short visits
python -m kanyo.detection.realtime_monitor \
  --config config_nest1.yaml

# Camera 2: Roosting location with long stays
python -m kanyo.detection.realtime_monitor \
  --config config_roost.yaml
```

**Different parameter profiles:**

**Active Nest (Nest1):**
```yaml
exit_timeout: 180              # Faster detection (3 min)
roosting_threshold: 3600       # Rarely roost (60 min)
roosting_exit_timeout: 600     # Standard (10 min)
activity_timeout: 120          # Precise activity (2 min)
```

**Roosting Location (Roost):**
```yaml
exit_timeout: 600              # Tolerant of gaps (10 min)
roosting_threshold: 900        # Quick roosting (15 min)
roosting_exit_timeout: 1200    # Long tolerance (20 min)
activity_timeout: 300          # Lenient activity (5 min)
```

### Seasonal Adjustments

Falcon behavior changes seasonally:

**Nesting Season (Spring/Summer):**
```yaml
roosting_threshold: 3600       # Less roosting, more activity
exit_timeout: 240              # Frequent short trips
activity_notification: true    # Track nesting behavior
```

**Migration/Winter:**
```yaml
roosting_threshold: 1200       # More roosting behavior
exit_timeout: 600              # Longer tolerance
activity_notification: false   # Less activity interest
```

### Custom Event Handlers

Extend the system by modifying `_handle_falcon_event()`:

```python
def _handle_falcon_event(self, event_type, timestamp, metadata):
    # Standard handling
    super()._handle_falcon_event(event_type, timestamp, metadata)

    # Custom analytics
    if event_type == FalconEvent.ROOSTING:
        self._update_roosting_analytics(metadata)

    # Custom alerting
    if event_type == FalconEvent.ACTIVITY_START:
        if self._is_unusual_activity_time(timestamp):
            self._send_special_alert("Unusual activity time", timestamp)
```

---

## Future Enhancements

Potential state machine improvements:

1. **Adaptive Thresholds**
   - Learn typical behavior patterns
   - Automatically adjust timeouts
   - Account for time-of-day patterns

2. **Confidence-Based Timeouts**
   - Shorter timeouts for high-confidence detections
   - Longer tolerance for low-confidence periods
   - Dynamic adjustment based on stream quality

3. **Multi-Falcon Tracking**
   - Separate state machines per individual
   - Track pair bonding behavior
   - Detect multiple occupants

4. **Behavioral Classification**
   - Distinguish hunting vs. preening
   - Detect feeding events
   - Identify mating behaviors

5. **Predictive Alerts**
   - Learn departure patterns
   - Predict likely departure times
   - Pre-generate clips before departure

---

## Conclusion

The falcon state machine provides intelligent, behavior-aware activity detection that eliminates false alerts while maintaining accurate behavioral records.

**Key Takeaways:**

1. **Start with defaults** - They work well for most deployments
2. **Tune gradually** - Change one parameter at a time
3. **Monitor logs** - Enable DEBUG temporarily to understand behavior
4. **Validate regularly** - Compare system reports with ground truth
5. **Document changes** - Track which parameters work for your site

**Success Metrics:**

- ✅ One arrival/departure pair per actual visit
- ✅ Activity periods logged but not alerting
- ✅ Visit durations match observed behavior
- ✅ No false departures during roosting
- ✅ Departure detection within tolerance window

**When properly tuned, you should see:**
- Clean, consolidated visit records
- Minimal notification spam
- Accurate behavioral analytics
- Reliable activity tracking during roosting

For questions or issues, refer to the [main README](../README.md) or open an issue on GitHub.
