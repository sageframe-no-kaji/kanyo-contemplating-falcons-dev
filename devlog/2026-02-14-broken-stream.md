# 2026-02-14: Stream Recovery & Timezone Bug Fix

## Summary

Implemented stream recovery feature for short YouTube outages, discovered and fixed a timezone bug causing 104 errors, and investigated false departure issues at Harvard cam.

## Features Implemented

### Stream Recovery (PENDING_RECOVERY State)

When a stream outage occurs (YouTube hiccup, network issue), the system now handles short outages gracefully:

**Before:** Any stream outage → state machine resets → false departure if bird was present

**After:** Outages ≤30s trigger recovery mode:
1. Save current state (VISITING/ROOSTING)
2. Enter PENDING_RECOVERY state
3. On reconnect, confirm bird still present (10s confirmation window)
4. If confirmed → restore previous state seamlessly
5. If not confirmed → trigger normal departure

New config options:
```yaml
stream_recovery_threshold: 30      # Max outage seconds to attempt recovery
stream_recovery_confirmation: 10   # Seconds to confirm bird after recovery
```

### Timezone Bug Fix

**Problem:** `_cancel_arrival()` and `_cancel_startup_presence()` in buffer_monitor.py used `datetime.now()` (no timezone) but `visit_start` was created with `get_now_tz()` (has timezone). This caused:

```
TypeError: can't subtract offset-naive and offset-aware datetimes
```

**Impact:** 104 errors in Harvard production logs

**Fix:** Changed `datetime.now()` → `get_now_tz(self.full_config)` in both cancel methods.

## Investigation: Harvard False Departures

### The Problem

User reported many short visits that didn't make sense. Investigation revealed:

| Visit | Duration | Issue |
|-------|----------|-------|
| falcon_110343 | 20s | Bird moved to extreme edge |
| falcon_114516 | 8s | YOLO detected "elephant" instead of bird |
| falcon_141959 | 44m → false departure | Bird rushed camera, still present at clip end |

### Root Cause: Camera Geometry

Harvard's camera is mounted in a way that falcons can:
1. Walk RIGHT UP to the lens (feathers fill frame)
2. Stand on the extreme edge of frame (barely visible)

When the bird does either:
- YOLO can't recognize a bird shape
- Detection drops to 0%
- After 90s exit_timeout → false departure triggered

**What YOLO sees when bird is close:**
```
YOLO found 1 objects: elephant(20):0.63  → "Falcon detected"
YOLO found 1 objects: person(0):0.89     → "Falcon detected"
```

The feather patterns confuse YOLO into thinking it's seeing elephant hide or human clothing.

### Analysis of Specific Clips

Downloaded and reviewed clips:
- `falcon_071401_arrival.mp4` - Normal arrival, "person" @ 0.89 confidence but worked fine
- `falcon_110343_visit.mp4` - Bird on edge, sudden close-up, 20s visit
- `falcon_114516_visit.mp4` - "Elephant" detection, bird on extreme edge
- `falcon_141959_departure.mp4` - Bird rushed camera, was still there at clip end
- `falcon_160843_departure.mp4` - Extreme edge for most of clip

### Could exit_timeout Help?

Analyzed gaps between departures and next arrivals:

| Departure | Next Arrival | Gap |
|-----------|--------------|-----|
| 11:04:03 (20s visit) | 11:34:28 | 30 min |
| 11:45:24 (8s visit) | 12:07:44 | 22 min |
| 02:19:59 (44m visit) | 04:01:46 | 1h 42m |

**Conclusion:** Gaps are 20+ minutes. If bird was just in blind spot, you'd see re-arrival within 1-2 minutes. These gaps confirm the bird really DID leave each time. The "edge of frame" shots are the bird *departing*, not just repositioning.

**Increasing exit_timeout would NOT help** for Harvard's camera geometry issues.

## Comparison: NSW vs Harvard

NSW/Orange cam has bird sitting in frame center - easy for YOLO.
Harvard's birds actively walk up to camera lens and sides.

This is a camera installation limitation, not a software bug.

## Potential Future Solutions

1. **Motion detection** - Detect presence even without recognizing bird shape
2. **Custom YOLO model** - Train on Harvard-specific close-up falcon views
3. **Region of interest** - Expect bird in certain areas, detect "something" vs "nothing"

## Files Changed

- `src/kanyo/detection/event_types.py` - Added PENDING_RECOVERY state
- `src/kanyo/detection/falcon_state.py` - Added recovery methods
- `src/kanyo/detection/buffer_monitor.py` - Added recovery logic + timezone fix
- `src/kanyo/utils/visit_recorder.py` - Made stream_recovery_threshold configurable
- `configs/config.template.yaml` - Added new config options
- `tests/test_falcon_state.py` - Added 8 tests for stream recovery
- `docs/sensing-logic.md` - Updated state diagram

## Deployment

- Pushed commit `c09ad34`
- Updated production configs on kanyo.lan
- Restarted containers with `update-code.sh`

## Lessons Learned

1. Always use timezone-aware datetimes consistently
2. Camera geometry affects detection reliability more than model accuracy
3. "Broken" visits may actually be real short visits - verify with gap analysis
4. YOLO misclassifications (elephant, person) are expected when close-up feathers fill frame
