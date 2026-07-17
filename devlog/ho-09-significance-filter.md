# Ho-09 — Significance Filter: Event Merging and Behavioral Significance

**Date authored:** 2026-07-16 (scaffolded post-ho-08; open questions now decidable)
**Status:** 📋 AUTHORED — ready for execution, **sequenced AFTER ho-12**
**Preceded by:** ho-08 (zombie ffmpeg fix); executes after ho-10/ho-11/ho-12
**Motivating incidents:** 2026-06-19 Fort Wayne nest swap storm (788 clips, 27 zombie ffmpegs, machine in swap); July 2026 UMass 1,350 events with median visit 19s
**Agent task series:** 025 (see `Agent-Tasks/025-significance-filter.md`)

---

## The problem this ho addresses

YOLO answers one question: *is there a bird in this frame?*

Kanyō currently treats every ARRIVED/DEPARTED pair as a significant event.
During active nesting — incubation swaps, brooding shifts, adults trading
nest duty — the state machine fires dozens of arrivals and departures per day
that are biologically routine and operationally noisy. The machine runs hot,
clips accumulate, and the viewer sees nothing but motion.

The user's framing: **"I don't care if birds are coming and going. I care if
something interesting is happening."**

The detection substrate is fine. The significance reasoning is missing.

**Sequencing note:** ho-12's presence layer removes the *manufactured* churn
(recognition gaps on roosting birds). What remains after ho-12 is *real*
churn — genuine short visits, genuine rapid swaps. This ho is the safety net
for that residual, and it is deliberately sequenced after ho-12 so it filters
real behavior, not recognition noise. Do not execute this ho before the 024
series is deployed and observed.

---

## What this ho is NOT

- Not an LLM/VLM feature. Significance filtering at this level is purely
  temporal and algorithmic.
- Not a YOLO replacement. YOLO (behind ho-12's presence layer) stays as the
  detection substrate.
- Not a model training session (that is ho-07).
- Not a state machine change. The state machine stays pure — it reports what
  happened; the filter decides what it means.

---

## Decisions (resolving the scaffold's open questions)

### Architecture: a distinct `EventSignificanceFilter`

Of the three options the scaffold named (state machine / `_handle_event` /
new layer), the answer is a **new class `EventSignificanceFilter` in
`src/kanyo/detection/significance_filter.py`**, sitting between state-machine
events and `event_handler`/notifications in `buffer_monitor`. The state
machine knows nothing about significance; `_handle_event`'s recording
mechanics (start/stop recorder, clip extraction) run on raw events exactly as
today. What the filter governs is the *surface*: notifications, event-store
rows, and which clips are kept and referenced.

This mirrors ho-12's move: the state machine stays a pure presence machine;
judgment layers sit beside it, each with one job.

### SWAP event vs silent merge: merge, flagged

No new event type. A DEPARTED→ARRIVED pair inside the merge window is a
*continuation of the same visit*: the merged `FalconVisit` row spans both
segments and carries a `merged_segments` count, so the viewer can render a
swap differently if it wants — but the event vocabulary (ARRIVED, DEPARTED,
ROOSTING) does not grow. A SWAP event would push behavioral interpretation
into the event layer, which is exactly what this ho keeps out of it.

### Merge window mechanics

When DEPARTED fires, the filter **holds** its surface effects (departure
notification, event-store append) for `merge_window_seconds` (default 300).

- **ARRIVED within the window** → continuation. No new notification, no new
  arrival clip surfaced (the continuation's arrival clip file is discarded),
  the held visit stays open, `merged_segments` increments. The window resets
  on each swallowed pair; recording mechanics run as normal underneath
  (a merged visit may span multiple visit files — acceptable; the row is the
  unit of meaning, the files are the unit of storage).
- **Window expires with no re-arrival** → the departure is real: notification
  sends (up to `merge_window_seconds` late — accepted cost, stated here
  deliberately) and the merged row is appended.

### Minimum significance

Detection-duration = `visit_end − visit_start` — both are detection
timestamps from the state machine, so this already excludes the
`exit_timeout` tail. Visits below `min_significant_seconds` (default 30) are
**recorded log-only**: no notification, event JSON row appended with
`insignificant: true`. The row still exists — data is never thrown away,
only de-surfaced.

This supersedes `short_visit_threshold`, which is validated in
`config.py:270` but consumed nowhere in the detection path — a dead key
documented as if it worked. 025-B marks it deprecated in the template.

### Activity-rate damping

Above `damping_arrivals_threshold` arrivals (default 8) within
`damping_window_hours` (default 1), the filter enters damped mode:
individual arrival/departure notifications are suppressed and replaced by a
periodic summary notification ("N visits in the last hour, median Xs").
Recording and event rows are unaffected. Damped mode exits when the rate
drops below threshold. Busy mode lives in the filter — not the state
machine, not `buffer_monitor` — answering the scaffold's question 1.

### Config surface

```yaml
significance_filter_enabled: true   # master switch; false = today's behavior
merge_window_seconds: 300           # DEPARTED→ARRIVED within this = same visit
min_significant_seconds: 30         # below this detection-duration: log-only
damping_arrivals_threshold: 8       # arrivals within the window to enter damped mode
damping_window_hours: 1             # rolling window for the arrival count
```

Backward compatibility: `significance_filter_enabled: false` (or `0` values)
reproduces current behavior exactly; cams opt in per config, answering the
scaffold's question 4. Per-cam tuning is the point — Fort Wayne nesting
season aggressive, Harvard conservative.

---

## Acceptance criteria

- DEPARTED followed by ARRIVED within `merge_window_seconds`: one merged
  visit row (correct span, `merged_segments` ≥ 2), no departure or arrival
  notification for the swallowed pair, continuation arrival clip discarded.
- DEPARTED with no re-arrival: notification and row released at window
  expiry, content identical to today's except timing.
- Visit under `min_significant_seconds`: row appended with
  `insignificant: true`, no notification.
- Arrival rate above threshold: individual notifications stop, summary
  notifications start; rate drops → normal notifications resume.
- Filter disabled: behavior byte-identical to current, verified by test.
- State machine and recording mechanics untouched — existing tests unmodified
  and green.

## Verification

```bash
black --check .
isort --check .
mypy src/
pytest
```

`mypy src/` is currently clean — keep it clean. `pytest` runs repo-wide with
coverage per `pytest.ini`. The filter ships with a dedicated test file
(`tests/test_significance_filter.py`) driving synthetic event sequences
through time; new logic must be well-tested. Historic repo-wide coverage is
~46% and this ho does not claim a repo-wide gate.

## Agent tasks

| Task | Scope |
|---|---|
| [025-A](Agent-Tasks/025-A-significance-filter-module.md) | `EventSignificanceFilter` module + `tests/test_significance_filter.py` |
| [025-B](Agent-Tasks/025-B-significance-filter-integration.md) | `buffer_monitor` wiring, `FalconVisit` flags, config keys + template, `short_visit_threshold` deprecation |

Execution order: 025-A → 025-B. **After ho-12 (024 series) is deployed and
observed** — this filter tunes against real residual churn, not recognition
noise.

---

## Deferred to future hos

- VLM "what is happening here?" reasoning layered on top (future ho).
- The Fort Wayne biological question — what inter-event interval indicates a
  meaningful behavioral change during incubation — is a tuning question
  against post-ho-12 data, not a design blocker; the config surface above is
  where its answer lands.
