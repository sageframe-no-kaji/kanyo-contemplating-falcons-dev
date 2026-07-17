# HO-10: Event Integrity & Instrumentation

**Date:** 2026-07-16
**Status:** 📋 AUTHORED — ready for execution
**Preceded by:** ho-09 scaffold (significance filter), 021 series (code stabilization)
**Agent task series:** 022 (see `Agent-Tasks/022-event-integrity-instrumentation.md`)

## Problem

The event pipeline produces records we cannot trust and lacks the observability
needed to tune it. Live evidence (July 2026): Harvard shows a 66%
arrival-cancellation rate plus stream flapping; UMass produced 1,350 events in
July with a median visit of 19 seconds. Before the structural fixes (ho-11
stream reader, ho-12 presence layer) can be tuned or even evaluated, the data
they will be tuned against has to be honest.

Six confirmed defects, all small and mechanical:

1. **`peak_confidence` is always 0.0.** All 1,449 production `FalconVisit`
   records show `peak_confidence: 0.0`. The field exists on the dataclass
   (`events.py:54`) and is serialized (`events.py:103`), but neither
   `FalconVisit` construction site in `buffer_monitor.py` (~421–430
   roosting-stop path, ~475–484 normal departure path) ever populates it.
   Nothing tracks a running max of detection confidence per visit.

2. **`departure_clip_path` is never written into event JSON**, even when the
   clip exists on disk. Same two construction sites; the field
   (`events.py:57`) is silently left `None`.

3. **ROOSTING always logs "(visit: 0s)".** `falcon_state.py:151` emits
   metadata key `visit_duration_seconds`; `event_handler.py:102` reads
   `metadata.get("visit_duration", 0)`. Key mismatch — the value is always
   the default 0. (The DEPARTED handler at `event_handler.py:79` reads the
   correct key; only ROOSTING is wrong.)

4. **Roosting-stop departure clips are structurally impossible.** In
   `roosting_recording_mode: stop`, DEPARTED fires with
   `event_time = last_detection` (`falcon_state.py:206`, `:241`), which is by
   definition ≥ `exit_timeout` (90s) in the past. The handler
   (`buffer_monitor.py:404–409`) calls `create_clip_from_buffer(event_time, …)`
   against a 60-second buffer. The requested window is always already evicted;
   `frame_buffer.extract_clip` logs "No frames found in range" and returns
   False. Silent failure, every time, by construction.

5. **"Stream reconnected" admin notifications fire without a matching "lost"
   alert** — observed 20+/day. In `capture.py frames()` the lost alert is
   throttled to once per hour (`capture.py:264–270`) but the reconnected
   alert is unconditional (`capture.py:276–277`). Every transient read
   failure that reconnects inside the throttle window produces an orphan
   "reconnected" message.

6. **No confidence observability.** Detection-poll confidence is only visible
   at DEBUG level, per frame. There is no aggregate view, so
   `detection_confidence` / `detection_confidence_ir` /
   `presence_sustain_confidence` (ho-12) cannot be tuned from data.

## Solution

Five bounded fixes plus one instrumentation add. No state machine changes, no
threshold changes, no architecture changes.

### 1 + 2: Visit record fields (022-A)

Track a running max detection confidence per visit in `BufferMonitor`
(updated on every detection poll, seeded from startup init detections, reset
on visit start and on every path that closes or cancels a visit). Write it
into both `FalconVisit` construction sites.

Populate `departure_clip_path` at both sites from
`get_output_path(clips_dir, visit_end, "departure", "mp4")` — the same
expression the clip manager uses to name the clip, so the paths agree by
construction. Because clip extraction is asynchronous
(`BufferClipManager._executor`), the path is recorded when the clip was
*scheduled*, not gated on `Path.exists()` (which would race the executor).

### 3: Metadata key fix (022-B)

`event_handler.py:102` reads `visit_duration_seconds`. One line plus a
regression test.

### 4: Departure-candidate clip snapshot (022-C)

The actual departure lies between the last successful roosting poll and the
first missed one — and at the moment of the first miss, that window is still
in the buffer. Mechanism: at the **first missed roosting poll** (detection
returns nothing where the previous poll detected), snapshot a
departure-candidate clip from the buffer covering
`[last_detection − clip_departure_before, now]`, written to the final
departure path with a `.mp4.tmp` suffix. If DEPARTED later fires, finalize
(rename to `.mp4`). If the bird re-confirms at a subsequent poll, discard the
candidate. Multiple miss/re-confirm cycles each discard and re-snapshot.

### 5: Reconnect alert gating (022-D)

Track outage-alert state in `StreamCapture`: only send "Stream reconnected"
when a corresponding lost alert was actually sent, then clear the flag.

### 6: Confidence summary (022-E)

Every `detection_summary_interval` seconds (default 300), emit one EVENT-level
rolling summary of detection polls since the last summary: poll count,
detection ratio, and min/median/max confidence across detected polls. This is
the data source for threshold tuning in ho-12 and beyond.

## Not in scope

- No changes to state machine logic, timeouts, or confirmation thresholds.
- No changes to the arrival/departure clip timing semantics (only whether the
  roosting-stop departure clip can exist at all).
- No presence logic (ho-12), no reader thread (ho-11), no event merging (ho-09).

## Acceptance criteria

- New `FalconVisit` rows carry a nonzero `peak_confidence` when the visit had
  detections, and a `departure_clip_path` whenever a departure clip was
  scheduled — in both the normal-departure and roosting-stop paths.
- ROOSTING log line shows the real visit duration.
- A roosting-stop departure produces a real departure clip showing the
  departure window, finalized from the candidate snapshot; a re-confirmed bird
  leaves no orphan `.tmp` files.
- "Stream reconnected" alerts are only ever preceded by a sent "lost" alert.
- A confidence summary line appears at the configured interval and is
  parseable (stable field order).
- Existing tests stay green; each fix lands with a focused regression test.

## Verification

```bash
black --check .
isort --check .
mypy src/
pytest
```

`mypy src/` is currently clean — keep it clean. `pytest` runs repo-wide with
coverage per `pytest.ini` (`--cov=src/kanyo --cov-report=term-missing`). New
logic must be well-tested; historic repo-wide coverage is ~46% and this ho
does not claim a repo-wide gate.

## Agent tasks

| Task | Scope |
|---|---|
| [022-A](Agent-Tasks/022-A-visit-record-fields.md) | `peak_confidence` tracking + `departure_clip_path` at both `FalconVisit` sites |
| [022-B](Agent-Tasks/022-B-roosting-duration-metadata-key.md) | ROOSTING `visit_duration` → `visit_duration_seconds` key fix |
| [022-C](Agent-Tasks/022-C-roosting-stop-departure-candidate-clip.md) | Departure-candidate clip snapshot at first missed roosting poll |
| [022-D](Agent-Tasks/022-D-reconnect-alert-gating.md) | Gate "reconnected" admin alert on a sent "lost" alert |
| [022-E](Agent-Tasks/022-E-detection-confidence-summary.md) | Periodic rolling detection-confidence summary |

Execution order: 022-B and 022-D first (one-liners, independent), then 022-A,
022-E, and 022-C last (only task with new mechanism). All five before ho-11.
