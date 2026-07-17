# HO-11: Stream Reader Thread & Single Time Authority

**Date:** 2026-07-16
**Status:** 📋 AUTHORED — ready for execution
**Preceded by:** ho-10 (event integrity), 021-F (spec'd but never landed — this ho supersedes and executes it)
**Agent task series:** 023 (see `Agent-Tasks/023-stream-reader-time-authority.md`)

## Problem

Two structural timing defects, one root: the main loop is both the frame
reader and the clock.

**Frozen reads are undetectable.** `cv2.VideoCapture.read()`
(`capture.py:238`) can block indefinitely on a stalled stream. Every watchdog
in the monitor runs *between* frames — the heartbeat, the
`time_since_last_frame` heuristic (`buffer_monitor.py:756–782`), the
frame-timeout warning — so a blocked read freezes the entire pipeline
silently. `VisitRecorder.write_frame(None)` already implements freeze-frame
fill and outage accounting (`visit_recorder.py:327–343`), but nothing ever
calls it with `None`: `StreamCapture.frames()` only yields real frames. This
was diagnosed in the 021 audit as 021-F, the one structural fix in that
series, and it never landed. The 2026-02-14 Harvard incident and the current
stream-flapping evidence both trace to this gap.

**Timestamps are stamped at processing, not capture.** Every timestamp in the
system comes from `get_now_tz()` called at processing time
(`buffer_monitor.py:228` in `process_frame`, `:884` in the skip path, and
throughout). When the pipeline stalls and then drains — a slow YOLO poll, an
encoder hiccup, a burst of buffered frames after a stream stutter — a run of
frames captured seconds apart gets stamped with nearly identical wall-clock
times ("burst-stamping"). Buffer time ranges, state machine absence math,
visit durations, and clip offsets all inherit the distortion.

## Solution

Reuse the 021-F design (option A, worker thread + queue — the recommended
one), updated for the current code, and extend it with the time-authority
rule 021-F didn't have.

### Reader thread + bounded queue + no-frame sentinel (023-A)

A worker thread inside `StreamCapture` runs `cap.read()` in a loop and pushes
frames into a bounded queue (drop-oldest when full, so a stalled consumer
sees fresh frames, not stale ones). `frames()` becomes a consumer:
`queue.get(timeout=stream_read_timeout_s)` (new config key, default 10.0).
On timeout it yields `None` — an explicit no-frame sentinel. A blocked
`cv2.read()` is now just a quiet queue, and the timeout sees it.

Read failures (`ret == False`) are signaled from the worker to the consumer,
which runs the **existing** lost-alert + `reconnect()` path — reconnection
and backoff logic stay exactly where they are (`connect()` owns all retry
timing). The worker is cleanly stoppable on disconnect: stop event, join, no
leaked threads, no zombie ffmpeg (ho-08 precedent).

### Frame timestamp assigned once, at read time (023-A + 023-B)

`Frame` (`capture.py:31`) gains a `timestamp: datetime` field, assigned
**once, in the reader thread, at read time**, using a clock callable injected
by the monitor (`get_now_tz` over the stream's configured timezone).

Every downstream consumer then uses `frame.timestamp` instead of calling
`get_now_tz()` at processing time: the frame buffer, the detector, the state
machine, the visit/arrival recorders, event timestamps. `process_frame` takes
the timestamp from the frame it is processing. This kills burst-stamping —
frames that queued during a stall carry the times they were actually read —
and establishes a single time authority for everything derived from a frame.
`get_now_tz()` remains only for non-frame contexts (startup, shutdown,
wall-clock scheduling).

### Sentinel consumption in the monitor (023-B)

When `frames()` yields `None`:

- If a visit is recording, call `visit_recorder.write_frame(None)` — the
  existing freeze-frame fill and `stream_outage_exceeded` accounting finally
  engage.
- Outage accounting (`state_machine.add_outage`, recovery-confirmation entry)
  keys off the sentinel stretch rather than being invisible during a blocked
  read.
- On the first real frame after an outage, the existing recovery-confirmation
  logic runs as today.

No second watchdog inside the per-frame loop — that was the broken approach
021-F explicitly ruled out. The signal comes from `capture`.

## Not in scope

- No changes to reconnect/backoff amounts, the daily attempt cap, or
  `connect()`'s signature.
- No changes to state machine thresholds or recovery-confirmation semantics.
- No presence logic (ho-12), no event-record changes beyond timestamps
  (ho-10 handles those).

## Acceptance criteria

- A capture source that goes silent for > `stream_read_timeout_s` produces
  `None` sentinels in the monitor loop — including when the underlying read
  is *blocked*, not just returning failure.
- An active recording during an outage receives `write_frame(None)` calls;
  `stream_outage_exceeded` fires when the threshold is passed.
- Reconnection preserves existing backoff exactly; recovery confirmation runs
  on the first real frame after outage.
- Every frame-derived timestamp in buffer, detector, state machine, and
  recorders equals the frame's read-time stamp; a simulated stall-then-drain
  produces monotonically spaced timestamps, not a burst of near-identical ones.
- Worker thread shuts down cleanly on `disconnect()` — no leaked threads.
- Normal operation shows no perf regression in the existing test loop.

## New configuration

```yaml
stream_read_timeout_s: 10.0   # seconds without a frame before the monitor
                              # sees a no-frame sentinel (outage handling)
```

Added to `DEFAULTS` in `config.py` and documented in
`configs/config.template.yaml` in the file's style.

## Verification

```bash
black --check .
isort --check .
mypy src/
pytest
```

`mypy src/` is currently clean — keep it clean. `pytest` runs repo-wide with
coverage per `pytest.ini`. New reader/sentinel/timestamp logic must be
well-tested (fake capture source driving frames → timeouts → frames);
historic repo-wide coverage is ~46% and this ho does not claim a repo-wide
gate.

## Agent tasks

| Task | Scope |
|---|---|
| [023-A](Agent-Tasks/023-A-reader-thread-timeout-sentinel.md) | Worker-thread reader, bounded queue, `None` sentinel, `stream_read_timeout_s`, read-time `Frame.timestamp` |
| [023-B](Agent-Tasks/023-B-frame-timestamp-authority.md) | Monitor consumes sentinel (freeze-frame + outage accounting); all downstream consumers switch to `frame.timestamp` |

Execution order: 023-A then 023-B. Both after the 022 series (ho-10) is green;
before ho-12 — the presence layer assumes trustworthy frame timestamps.
