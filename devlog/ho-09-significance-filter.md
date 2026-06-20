# Ho-09 — Significance Filter: Event Merging and Behavioral Significance

**Status:** Scaffold — awaiting authoring session  
**Preceded by:** ho-08 (zombie ffmpeg fix)  
**Motivating incident:** 2026-06-19 Fort Wayne nest swap storm; 788 clips, 27 zombie ffmpegs, machine in swap

---

## The problem this ho addresses

YOLO answers one question: *is there a bird in this frame?*

Kanyō currently treats every ARRIVED/DEPARTED pair as a significant event. During active nesting — incubation swaps, brooding shifts, adults trading nest duty — the state machine fires dozens of arrivals and departures per day that are biologically routine and operationally noisy. The machine runs hot, clips accumulate, and the viewer sees nothing but motion.

The user's framing: **"I don't care if birds are coming and going. I care if something interesting is happening."**

YOLO has met its match on nest cams. The detection substrate is fine. The significance reasoning is missing.

---

## What this ho is NOT

- Not an LLM/VLM feature. Significance filtering at this level is purely temporal and algorithmic.
- Not a YOLO replacement. YOLO stays as the per-frame detection substrate.
- Not a model training session (that is ho-07).
- The VLM "what is happening here?" capability is a future ho (ho-10 or later) that layers on top of this one.

---

## Core concepts to design

### 1. Event merge window

If DEPARTED → ARRIVED fires within N minutes, treat it as a **continuous event** (or a named `SWAP` event) rather than two separate events. No new clip. No new notification. The existing visit extends or is flagged.

Key decisions:
- What is N? (Per-cam config. Fort Wayne nesting season: aggressive. Harvard normal: conservative.)
- Does a swap extend the existing visit recording, or start a new one silently?
- Does the merge window reset on each swap, or is there a maximum merged visit duration?

### 2. Minimum visit significance

Visits with detection duration below M seconds are **logged only** — no clip, no notification, no full recording pipeline triggered. M is configurable per cam.

Key decisions:
- What counts as "detection duration"? (Time between first and last YOLO hit, not wall-clock visit duration including exit_timeout.)
- Does the clip still get made but not surfaced? Or does recording not start at all?

### 3. Activity-rate damping

If more than N ARRIVED events fire in the last H hours, the cam enters **busy mode**: notifications suppressed, recording continues, clips are made but flagged as routine. Busy mode resets when the rate drops.

Key decisions:
- Where does busy mode live — state machine, buffer_monitor, or a new layer?
- How is "busy" surfaced to the viewer? (Different clip label? Separate feed?)

---

## Architecture questions

1. **Where does the merge window live?** Options:
   - In `FalconStateMachine` — cleanest, but state machine currently knows nothing about time between events
   - In `BufferMonitor._handle_event` — pragmatic, but adds logic to an already busy method
   - In a new `EventSignificanceFilter` class between the state machine and the event handler

2. **What is the SWAP event?** Currently the event types are ARRIVED, DEPARTED, ROOSTING. A SWAP (or NEST_SWAP) event might be cleaner than a merge — it names what happened, lets the viewer display it differently, and doesn't conflate "bird came and went" with "birds traded duty."

3. **Config surface:** What does the config.yaml interface look like? Proposal:
   ```yaml
   event_merge_window_minutes: 10       # merge DEPARTED+ARRIVED within this window
   min_detection_duration_seconds: 30   # below this, log-only (no clip, no notify)
   busy_mode_threshold_per_hour: 8      # enter busy mode above this arrival rate
   ```

4. **Backward compatibility:** Cams in normal operation (Harvard, NSW) should not be affected unless they opt in with non-zero values.

---

## Files likely touched

- `src/kanyo/detection/falcon_state.py` — possibly; merge window might need state machine awareness
- `src/kanyo/detection/buffer_monitor.py` — merge window logic if kept in event handler
- `src/kanyo/detection/event_types.py` — new SWAP event type (possibly)
- `src/kanyo/utils/config.py` — new config keys and defaults
- `src/kanyo/utils/config_template.yaml` / `configs/config.template.yaml` — document new keys
- Tests: new test file `tests/test_significance_filter.py`

---

## Open questions for authoring session

1. SWAP as a named event vs. silent merge — which model is cleaner for the viewer?
2. Does the merge window need to know what the state machine's current visit looks like (duration, detection count) to decide whether to merge or split?
3. Fort Wayne-specific: during active incubation, what interval between events actually indicates a meaningful behavioral change rather than a routine swap? (Biological question with engineering implications.)
4. Does busy mode belong in this ho or a subsequent one?
