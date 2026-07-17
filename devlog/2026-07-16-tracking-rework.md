# 2026-07-16: The Tracking Rework

## Summary

One day, four hos, sixteen detector PRs (#31–46), four viewer PRs (#4–7). The
false entry/exit problem that has dogged this system for months is not one bug
— it is three layers of the pipeline each being wrong in its own way, and every
prior fix hardened the wrong layer. This rework repaired all three: the records
are now honest (ho-10), the clock is now honest (ho-11), presence is now a
judgment over evidence instead of a per-frame recognition bit (ho-12), and a
significance filter (ho-09) sits on top to keep real churn from flooding the
surface. Plus a coverage campaign to 100%, an image-based deployment route, and
two smaller production fixes. v1.0.0 ships this work.

## The Problem

Since the system went live, roosting birds have been generating false
departures and re-arrivals — a sleeping falcon that never moved would "leave"
and "return" over and over. I have attacked this repeatedly, and the ho chain
records each attempt:

- **exit_timeout debounce** — don't declare departure until 90s of no
  detection.
- **State machine redesign** (ho-05.7) — formal states instead of ad-hoc
  flags.
- **Arrival confirmation** (ho-06.13) — require 30% detection over 10s before
  declaring arrival.
- **Outage compensation** (ho-05.71) — don't let stream drops read as
  departures.
- **Recovery states** (2026-02-14, `PENDING_RECOVERY`) — restore
  VISITING/ROOSTING across short outages.
- **Roosting mode** (task 020) — stop recording marathon roost sessions.

Every one of these made the state layer smarter about a recognition layer that
kept flapping. None of them questioned the recognition layer itself. The false
events kept coming because the state machine was being fed garbage, and no
amount of debounce turns garbage into signal.

## The Diagnosis: Three Layers

When I finally sat down and traced the whole pipeline instead of patching the
symptom, the problem resolved into three distinct layers.

### 1. Recognition

yolov8n answers a single question per frame: *is there a recognizable bird
shape here?* That binary flaps on motionless birds — a sleeping falcon
flickers in and out of recognition frame to frame. And Harvard's camera
geometry lets birds walk right up to the lens or stand at the frame edge,
where YOLO sees feather texture and reports `elephant @ 0.63` or
`person @ 0.89` (logged at Harvard, documented in the 2026-02-14
investigation) — or nothing at all. The bird is plainly there; recognition
says absent.

### 2. State

Departure is pure absence-of-detection for `exit_timeout` (90s). That is
asymmetric with arrival, which requires positive confirmation (30% detection
over 10s). So the state machine demands evidence to let a bird in, but lets it
"leave" on nothing more than a recognition gap. Every gap longer than 90s on a
bird that never moved manufactures a DEPARTED, and the next successful frame
manufactures an ARRIVED. Exit + entry pairs from nothing.

### 3. Timing

Every timestamp in the system came from `get_now_tz()` called at *processing*
time, not capture time. A stall-then-drain (slow YOLO poll, encoder hiccup,
buffered frames after a stutter) burst-stamped a run of frames with nearly
identical wall-clock times, and buffer ranges, absence math, durations, and
clip offsets all inherited the distortion. Worse: a frozen `cv2.read()` was
undetectable — every watchdog ran *between* frames, so a blocked read froze
the whole pipeline silently. This was diagnosed back in the 021 audit as
021-F, the one structural fix in that series, and it never landed. And in
roosting-stop mode, departure clips were structurally impossible: DEPARTED
fires with an event time at least 90s in the past, and the handler asked a
60-second buffer for a window that was always already evicted. Silent failure,
every time, by construction.

## The Live Evidence (July)

Pulling production numbers made the case unambiguous:

- **Harvard:** 99 events in July, but a **66% arrival-cancellation rate** —
  on 07-14 alone, 29 pending arrivals with 19 cancelled, confirmation ratios
  of 2.8–9.1% against the 30% bar. The machine spends most of its time
  starting arrivals it then abandons. Plus ~17 stream losses per day.
- **UMass:** 1,350 events in July. Median visit: **19 seconds**. 70% under a
  minute. That is not falcon behavior; that is recognition flapping.
- **`peak_confidence: 0.0` on all 1,449 event records.** The field existed,
  was serialized, and was never populated — so no tuning data existed at all.
- **Every ROOSTING log line said "(visit: 0s)"** — a metadata key mismatch
  meant the real duration was never read.

The instrumentation gaps were as damning as the false events: I could not have
tuned the fixes even if I'd had them, because the system recorded nothing
usable about its own detection quality.

## What Shipped

### ho-10 — Event Integrity & Instrumentation (PRs #31–35)

Make the records honest before touching the architecture. Five bounded fixes
plus one instrumentation add:

- `peak_confidence` and `departure_clip_path` now populated in visit records,
  both construction paths (022-A, #33).
- ROOSTING duration key mismatch fixed — log lines show real visit durations
  (022-B, #31).
- Roosting-stop departure clips now exist: a departure-candidate clip is
  snapshotted from the buffer at the *first missed poll* — while the window is
  still in the buffer — and finalized if DEPARTED fires, discarded if the bird
  re-confirms (022-C, #35).
- "Stream reconnected" alerts gated on an actually-sent "lost" alert — no
  more orphan reconnect notifications (022-D, #32).
- A rolling detection-confidence summary every 5 minutes: poll count,
  detection ratio, min/median/max confidence. This is the data source for all
  future threshold tuning (022-E, #34).

### ho-11 — Stream Reader Thread & Single Time Authority (PRs #36–37)

The structural timing fix — the one 021-F specified and never landed. Frame
reading moves to a worker thread with a bounded queue; the consumer times out
after `stream_read_timeout_s` and yields an explicit no-frame sentinel, so a
*blocked* `cv2.read()` is finally visible (023-A, #36). Every frame gets its
timestamp **once, at read time**, and every downstream consumer — buffer,
detector, state machine, recorders, event timestamps — uses `frame.timestamp`
instead of calling the wall clock at processing time. Burst-stamping is dead.
The sentinel path finally engages the freeze-frame fill and outage accounting
that `write_frame(None)` had implemented all along but nothing ever called
(023-B, #37).

### ho-12 — Presence Layer (PRs #38–40)

The architectural centerpiece. A new `PresenceTracker` sits between the
detector and the state machine; the state machine keeps consuming a boolean,
untouched, but the presence layer now *decides* that boolean from richer
evidence:

- A **region** tracking the last confirmed detection, following the bird.
- **Dual thresholds**: entering presence stays exactly as strict as today
  (target-class ≥ `detection_confidence`), but *sustaining* presence needs
  only a low-confidence box of **any class** overlapping the region — 
  Harvard's "elephant" and "person" misclassifications are strong evidence
  the bird is still there (024-A raw-detections path, #38).
- **Motion evidence**: cheap frame differencing inside the region, with a
  global-change discount so IR/day flips and exposure swings never read as
  bird motion (024-B, #39).
- The core rule: **no detection AND no region motion → still present.** A
  sleeping bird produces exactly that signature. Absence of recognition is no
  longer evidence of absence. Exit requires positive evidence (a motion burst
  leaving the region, then quiet), with a one-hour zero-evidence failsafe so
  a missed departure can never hold presence forever.
- Wired in behind `presence_enabled`; disabled reproduces the old behavior
  exactly, verified by test (024-C, #40).

### ho-09 — Significance Filter (PRs #41–42)

ho-12 removes the *manufactured* churn; what remains is *real* churn — genuine
short visits, genuine rapid nest swaps. `EventSignificanceFilter` governs the
surface (notifications, event rows, which clips are referenced) without
touching recording mechanics: a DEPARTED→ARRIVED pair inside
`merge_window_seconds` merges into one visit row (`merged_segments` counted,
no new event vocabulary); visits under `min_significant_seconds` are recorded
log-only with `insignificant: true` — data is never thrown away, only
de-surfaced; above `damping_arrivals_threshold` arrivals per hour, individual
notifications are replaced by a periodic summary (025-A #41, 025-B #42).
Per-cam tuning is the point — the deploy plan sets Fort Wayne aggressive,
Harvard conservative.

### Production fixes and hardening (PRs #43–46)

- **026 — admin today-visits timezone (#43):** finished what 021-G started —
  `get_today_visits()` and file-browser mtimes are now stream-timezone aware.
  Harvard's overview card no longer shows 0 visits all evening after the UTC
  date rolls over.
- **Coverage campaign (#44):** `src/kanyo` went from 46% at the start of this
  rework (78% after the hos landed their own tests) to **100%**, with the
  floor now enforced at 95% (`--cov-fail-under=95`).
- **Docker deploy route (#45):** detectors move from a src-mounted stale
  image (built 2026-04-21) to pinned, published images. Deployment plan
  written (`docs/deployment-kanyo.md`); v1.0.0 is the first pinned release.
- **027 — ffmpeg log leak (#46):** the success-path cleanup computed a log
  filename that never exists (`with_suffix` on the renamed `.mp4` path), so
  every confirmed clip left a stray `.ffmpeg.log` in production. One naming
  helper now owns the convention; the regression test fails on the old code.

### Viewer (kanyo-viewer PRs #4–7)

- **#4:** accept microsecond clip filenames from detector 021-I, with
  backward compatibility for archived pre-microsecond dates.
- **#5:** `events_YYYY-MM-DD.json` becomes the authority for visit
  boundaries, durations, and counts — the viewer stops inferring roost
  durations from clip files, so true roost durations display.
- **#6:** all event times render in the *stream's* timezone — visitors see
  the falcon's clock, not their own or the server's.
- **#7:** full detector↔viewer contract alignment; the README data-contract
  section now tells the truth about what the detector writes.

## The Timezone Findings

I went into this convinced there was a deep timezone bug — event times looked
wrong in the admin dashboard. The investigation found something better:

- **Telegram captions were always correct.** All sites have proper IANA
  timezones configured, and the notification path used them correctly all
  along.
- The visible symptom was the **stale admin dashboard image** — the running
  container was built 2026-04-21, *before* 021-G's timezone handling landed.
  The fix has existed in the repo for months; the deployed image predated it.
  The rebuild happens at deploy.
- The one residual real bug was `get_today_visits()` computing "today" from
  naive server time (UTC in containers) — fixed in 026 (#43).
- The logger dual-stamps **by design**: UTC prefix on every line, stream-local
  times in the event text. That is a feature, not a bug — the prefix
  correlates across sites; the event text matches what the camera sees.

## What This Fixes, in Plain English

- A sleeping bird no longer "departs" when YOLO blinks.
- Departures are now observed motion events, not recognition gaps.
- A bird at the lens or frame edge stays present, even when YOLO calls it an
  elephant.
- Rapid exit/re-entry pairs merge into one visit.
- Nest-swap storms are damped into summary notifications.
- Frozen streams are detected instead of silently hanging the pipeline.
- Timestamps come from when the frame was read, not when it was processed.
- Roosting departure clips exist (they were structurally impossible before).
- The viewer shows the falcon's timezone and true roost durations.
- The admin dashboard shows the correct "today."
- Confidence data is recorded, so future tuning is grounded in evidence.
- A 95% coverage gate holds the line on all of it.

## Deferred / Follow-ups

- **yolov8s upgrade** — a separate deploy, *after* the confidence-summary
  data accumulates. Weights are baked into the image, so it ships as a new
  image version. (This supersedes the old task-015 plan.)
- **Detector row enhancements** — `visit_clip_paths`, a provisional
  in-progress row, microseconds in the `id`, basename standardization.
  Surfaced by the viewer contract-alignment pass; queued for a future ho.
- **One-time host cleanup** of the stray `*.ffmpeg.log` files accumulated
  before 027 — goes in the deploy run, not in code.
- **Admin dashboard image rebuild** — happens at deploy (Phase 2 of the
  plan); that alone clears most of the visible timezone symptoms.
- **Viewer eslint debt** — 6 pre-existing errors, untouched by this work.

## Deployment

The full plan is `docs/deployment-kanyo.md`: tag `v1.0.0` → CI publishes
`1.0.0-nvidia` to GHCR → viewer pull/build → dashboard rebuild → per-site
config tuning (Fort Wayne, Harvard) → canary Harvard against a log-verification
checklist → roll the fleet. Rollback is layered — image repoint in seconds,
full template rollback, or per-site behavioral rollback via
`presence_enabled: false` / `significance_filter_enabled: false` with no image
change.

**Deploy is currently blocked:** Tailscale is logged out on the Mac and needs
an interactive re-login before the host is reachable. The moment that clears,
the plan executes top to bottom.
