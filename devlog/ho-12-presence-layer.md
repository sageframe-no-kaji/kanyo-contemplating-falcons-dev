# HO-12: Presence Layer

**Date:** 2026-07-16
**Status:** 📋 AUTHORED — ready for execution
**Preceded by:** ho-11 (stream reader + time authority — presence assumes trustworthy frame timestamps)
**Context:** `devlog/2026-02-14-broken-stream.md` (Harvard camera-geometry investigation), `devlog/adaptive-sampling-idea.md.md` (motion-gating direction)
**Agent task series:** 024 (see `Agent-Tasks/024-presence-layer.md`)

## Problem

The system equates *presence* with *single-frame YOLO recognition*, and the
two are not the same thing. That single conflation is the architectural root
of the false entry/exit storm:

- **Motionless roosting birds flap.** yolov8n's binary presence on a sleeping
  bird flickers frame to frame. Departure is pure absence-of-detection for
  `exit_timeout` (90s, `falcon_state.py:191–252`) — asymmetric with the
  30%-over-10s arrival confirmation — so a recognition gap on a bird that
  never moved manufactures a DEPARTED, and the next successful frame
  manufactures an ARRIVED. Exit + entry pairs from nothing.
- **At-lens and frame-edge birds misclassify.** Harvard's geometry lets birds
  walk up to the lens or stand at the frame edge; YOLO then sees
  "elephant @ 0.63" or "person @ 0.89" (2026-02-14 investigation) or nothing
  at all. The bird is plainly there; recognition says absent.
- **Live evidence:** Harvard 66% arrival-cancellation thrash; UMass 1,350
  events in July, median visit 19s.

ho-10 made the records honest and ho-11 made the timestamps honest. This ho
is the architectural centerpiece: presence becomes a *reasoned judgment over
richer evidence* — recognition at two thresholds, spatial continuity, and
cheap motion — instead of a per-frame recognition bit.

## Solution

New module `src/kanyo/detection/presence.py`, class `PresenceTracker`,
sitting **between** `FalconDetector` and `FalconStateMachine` in
`buffer_monitor`. The state machine keeps consuming a boolean — its logic,
timeouts, and tests are untouched. The presence layer decides that boolean.

### Region

The tracker maintains a presence region: the last confirmed YOLO bbox plus a
configurable margin (`presence_region_margin_frac`), updated on every
detection at or above the sustain threshold. The region follows the bird: on
motion-only evidence, it shifts toward the motion centroid, bounded per
update and clamped to the frame.

### Dual thresholds

YOLO runs once per poll at a low floor; logic thresholds apply in code:

- **ENTER** presence requires a target-class detection ≥
  `detection_confidence` — existing semantics, feeding the existing arrival
  confirmation window unchanged. Entering presence stays exactly as strict as
  it is today.
- **SUSTAIN** presence requires only a low-confidence box
  (`presence_sustain_confidence`, default 0.15) of **any class** overlapping
  the region. This is deliberate: Harvard's at-lens "elephant" and "person"
  misclassifications are strong evidence the bird is still there.

The detector currently thresholds inside the model call
(`detect.py:153–157`); it gains a raw-detections path (single inference at
the floor, post-filtered in code) so existing `detect_birds()` semantics are
preserved for arrival while the tracker sees everything.

### Motion evidence

Cheap frame differencing evaluated per poll: grayscale, downscaled, absdiff
against the previous poll frame, pixel threshold
(`presence_motion_pixel_threshold`), changed-area fraction. Motion evidence =
changed fraction inside region+margin ≥ `presence_motion_min_area_frac`.

**Global-change discount:** if the whole-frame changed fraction exceeds
`presence_global_change_frac`, that frame's motion evidence is ignored
entirely — IR/day flips, exposure swings, and camera adjustments must not
read as bird motion (or as departures).

### Presence logic

While present:

- Sustain-level detection overlapping the region → present.
- Region motion (not globally discounted) → present.
- **No detection AND no region motion → STILL PRESENT.** A sleeping bird
  produces exactly this signature. This is the core fix: absence of
  recognition is no longer evidence of absence.

Exit requires **positive evidence**:

- A motion burst in/leaving the region followed by quiet with no
  sustain-level detection → the tracker reports absent, and the existing
  `exit_timeout` runs from there (the state machine remains the debounce; any
  renewed evidence flips the tracker back and resets absence as today). OR —
- **Failsafe:** `presence_absence_failsafe_seconds` (default 3600) with zero
  evidence of any kind — no detection at any threshold, no motion — so a
  missed departure can never hold presence forever.

While absent, the tracker's output is simply the ENTER condition, so
arrival semantics (and the confirmation window) are byte-identical to today.

### Configuration

Added to `DEFAULTS` in `config.py` and documented in
`configs/config.template.yaml` in the file's documentation style:

```yaml
presence_enabled: true                    # presence layer on/off (off = today's behavior)
presence_sustain_confidence: 0.15         # any-class floor to sustain presence in region
presence_region_margin_frac: 0.25         # region = last bbox + this fraction of bbox size
presence_motion_pixel_threshold: 25       # grayscale absdiff threshold per pixel
presence_motion_min_area_frac: 0.02       # changed fraction of region to count as motion
presence_global_change_frac: 0.5          # whole-frame change above this = discard motion evidence
presence_absence_failsafe_seconds: 3600   # zero-evidence ceiling before forced absence
```

With `presence_enabled: false` the pipeline behaves exactly as before — the
boolean fed to the state machine is `len(detections) > 0`, verified by test.

## Not in scope

- No state machine changes — it consumes the same boolean with the same
  timeouts.
- No changes to arrival confirmation, recording, or clip mechanics.
- No model change or training (ho-07/015-series territory).
- No event merging or notification damping (ho-09 — sequenced after this ho).

## Acceptance criteria

- A parked (motionless) synthetic blob that YOLO stops detecting is sustained
  as present — no DEPARTED until positive exit evidence or failsafe.
- A blob that moves out of frame (motion burst, then quiet, no sustain
  detection) allows the departure path: tracker reports absent and
  `exit_timeout` runs.
- A whole-frame flip (IR/day) is discounted: no motion evidence, no state
  change from that frame.
- Any-class low-confidence boxes overlapping the region sustain presence
  (Harvard elephant/person case).
- Zero evidence for `presence_absence_failsafe_seconds` forces absence.
- ENTER semantics unchanged: presence begins only on target-class detection ≥
  `detection_confidence`, and arrival confirmation behaves exactly as today.
- `presence_enabled: false` reproduces current behavior exactly.

## Verification

```bash
black --check .
isort --check .
mypy src/
pytest
```

`mypy src/` is currently clean — keep it clean. `pytest` runs repo-wide with
coverage per `pytest.ini`. The presence module ships with unit tests over
synthetic numpy frames: moving blob, parked blob with detection dropout (must
sustain), blob exiting frame (must allow departure), global-flip frame (must
discount), failsafe expiry. New logic must be well-tested; historic repo-wide
coverage is ~46% and this ho does not claim a repo-wide gate.

## Agent tasks

| Task | Scope |
|---|---|
| [024-A](Agent-Tasks/024-A-detector-raw-detections.md) | `FalconDetector` raw-detections path (single low-floor inference, post-filtered) |
| [024-B](Agent-Tasks/024-B-presence-tracker-module.md) | `presence.py` / `PresenceTracker` + synthetic-frame unit tests |
| [024-C](Agent-Tasks/024-C-presence-integration-config.md) | `buffer_monitor` integration, config keys, template documentation |

Execution order: 024-A → 024-B → 024-C. After ho-11 lands (frame timestamps
must be trustworthy before presence reasons over them); before ho-09.
