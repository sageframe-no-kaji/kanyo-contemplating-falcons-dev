# YouTube Stream Access — Constraints and Operational Expectations

Kanyō sources its video from public YouTube live streams. That access works,
but it operates in a gray area: YouTube's anti-abuse systems are built to stop
scrapers, and a 24/7 automated stream monitor looks like one. This document
records how access works, the failure modes we have hit in production, what an
outage looks like from the operator's seat, and the constraints anyone
deploying this system inherits. All of it is grounded in incidents that
actually happened (#9, #10, #11, #13).

The one-line summary: **this system cannot promise 100% uptime on
YouTube-sourced streams.** YouTube can change its abuse detection at any time,
and has. Design your expectations around graceful degradation, not perfect
capture.

---

## How access works

`StreamCapture` (`src/kanyo/detection/capture.py`) resolves the YouTube watch
URL to a direct HLS URL with yt-dlp, then reads it with OpenCV/FFmpeg:

```
yt-dlp --js-runtimes node -f "best[height<=720]" -g <watch-url>
```

- **JS runtime is required.** YouTube gates stream access behind a JavaScript
  "n-challenge" that yt-dlp solves via the `yt-dlp-ejs` package and a Node 20
  runtime. The Docker images install Node 20 (Ubuntu 22.04's Node 12 lacks the
  `--experimental-permission` sandbox flag the solver uses) and pass
  `--js-runtimes node` on every invocation. This was the fix for the April
  2026 bot-detection outage (#9).
- **Fallback client.** When resolution fails with `Precondition check
  failed`, `connect()` retries once with
  `--extractor-args youtube:player_client=android_creator`. The fallback flag
  resets on the next successful connection.
- **Cookies.** A shared `cookies.txt` lives at `${KANYO_CODE_ROOT}/cookies.txt`
  on the host and is mounted read-write into every stream container (rw so
  yt-dlp can refresh rotating cookie values). It exists for streams that
  require authenticated access. The default detection path does **not** pass
  `--cookies` — for public streams it is a no-op, and stale cookies can
  actively break capture (upstream yt-dlp #16507: cookies causing spurious
  "live event has ended" errors). The `--cookies` flag was deliberately
  removed from the detection invocation when the backoff system landed
  (commit `2bcf04c`).
- **yt-dlp version floor.** `yt-dlp>=2024.12.01` is pinned in the
  requirements files, and the Docker images upgrade yt-dlp (plus
  `yt-dlp-ejs`) at build time. YouTube changes its player API periodically and
  older yt-dlp versions simply stop working; when capture breaks after a
  YouTube-side change, pulling a fresh image (or rebuilding) is the first
  move.

Once resolved, the direct URL feeds continuous HLS segment fetches, 24/7, per
stream. That request pattern is the root of everything below.

---

## Failure modes seen in production

### Bot detection on unauthenticated clients (#9, April 15 2026)

YouTube began requiring JS-capable, browser-like requests for yt-dlp stream
access. Both GPU containers failed to open their streams and the detection
system went fully offline. Fix: the Node 20 runtime, `yt-dlp-ejs`, and
`--js-runtimes node` described above. This class of failure presents as
yt-dlp resolution errors in the logs — the stream never opens at all.

### IP-level 403 soft-ban (#10, April 18 2026)

The serious one. The YouTube CDN returned HTTP 403 with an empty body for
**every HLS segment request** from the production IP, while manifest URLs
still resolved normally. Systematic testing confirmed an IP-level block, not a
request-shape or authentication problem:

| Test | Result |
| --- | --- |
| Segment fetch from production IP (residential) | 403, 0 bytes |
| Same fetch from a phone-hotspot IP | 200 |
| A *different* stream from the production IP | 403 |
| Same stream, same IP, browser user-agent | 403 |
| yt-dlp direct download from the production IP | 403 |

Two compounding findings:

- **IP reputation is the primary signal.** Residential IPs generating
  continuous automated fetches get flagged; datacenter IPs are expected to
  produce automated traffic and tolerate the same pattern better (#13).
- **TLS fingerprinting is in play.** With identical logged-in cookies, segment
  fetches from the production host's Linux/OpenSSL TLS fingerprint returned
  403 while macOS curl on the *same IP* returned 200. Google's anti-abuse
  layer appears to use JA3/JA4-style fingerprinting alongside IP reputation.
  Matching headers and cookies is not enough to look like a browser.

The proximate cause was request volume: the old reconnection logic (fixed 5s
delay scaling to 5 min, no jitter, no cap) produced 1000+ reconnect attempts
per day during a sustained outage — enough to trip the anti-abuse heuristics
on a residential IP.

### Routine stream flapping

Even healthy streams drop constantly — YouTube encoder restarts, network
blips, CDN hiccups. Production numbers from July 2026 showed roughly **17
stream losses per day** on the Harvard stream alone
(`devlog/2026-07-16-tracking-rework.md`). This is normal background noise, not
an incident. The reconnect path absorbs it; the presence layer and outage
accounting keep short drops from corrupting event records.

### yt-dlp version rot

YouTube periodically changes its API and older yt-dlp versions stop resolving
streams. Symptom: resolution failures across all streams after a period of
stable operation, with no 403s. Remedy: pull a floating-tip image or rebuild
so the build-time `pip install --upgrade yt-dlp yt-dlp-ejs` picks up the fix.

---

## The backoff system

The response to #10 (issue #11, commit `2bcf04c`). All reconnect timing is
owned by `connect()` in `capture.py`; `frames()` and `reconnect()` never sleep
independently, so delays cannot double up.

Constants (top of `capture.py`):

| Constant | Value | Meaning |
| --- | --- | --- |
| `BACKOFF_MIN_SECONDS` | 60 | first retry delay |
| `BACKOFF_MAX_SECONDS` | 1800 | ceiling (30 min) |
| `BACKOFF_MULTIPLIER` | 2.0 | exponential growth per consecutive failure |
| `BACKOFF_JITTER_FRAC` | 0.2 | ±20% jitter on every delay |
| `MAX_DAILY_ATTEMPTS` | 50 | hard cap per stream per rolling 24h window |

Behavior:

- Delays run 60s → 120s → 240s → … → 1800s, with ±20% jitter, and reset to
  zero on any successful connection.
- Each connection attempt counts against the daily cap. At 50 attempts in a
  24h window the stream goes **dormant**: `connect()` sleeps an hour, rechecks
  the window, and only resumes attempting when the window resets. Worst-case
  YouTube-facing load during a total outage is 50 attempts/day per stream —
  by design, well under what triggered the ban.

Log lines to know:

```
Backoff: sleeping 240s before next attempt (failure #3)
Daily attempt cap (50) reached for this stream. Going dormant until window resets.
✅ Connected to stream
✅ Reconnected successfully!
```

A rising `failure #N` with growing sleeps is the system riding out an outage
correctly. The dormancy message means the stream has been down for many hours
— at that point the question is *why*, not whether the retry loop is working.

---

## What an outage looks like to an operator

Mechanics (ho-11 reader-thread architecture): a worker thread reads frames
into a bounded queue. A failed read pushes a failure marker and triggers the
reconnect path; a *blocked* read — stream silently frozen — surfaces as a
no-frame timeout after `stream_read_timeout_s`, and `frames()` yields an
explicit no-frame sentinel. Downstream, the sentinel engages freeze-frame fill
and outage accounting, so stream drops do not read as bird departures and
short outages do not split or corrupt visits.

Notifications:

- **"Stream connection lost: \<url\>"** — admin alert, throttled to once per
  hour per stream.
- **"Stream reconnected"** — sent only when a matching "lost" alert actually
  went out, so connectivity alerts arrive in pairs and a quiet log means a
  quiet stream.
- During a suspected ban, `ops/ban-watch.sh` sends ntfy status updates
  ("still banned" every 2h, "unbanned" on recovery).

### What self-heals

- Brief drops, encoder restarts, network blips — reconnect + backoff, no
  action needed. Expect on the order of a dozen-plus per day per stream.
- `Precondition check failed` — automatic one-shot fallback to the
  `android_creator` player client.
- Multi-hour YouTube-side outages — backoff rides them out at the 30-minute
  ceiling; the stream recovers on its own when YouTube does.

### What needs intervention

- **Persistent 403s across all streams** — almost certainly an IP ban. See
  recovery below. The system will not fix this by retrying; the cap exists to
  make sure it doesn't make it worse.
- **Resolution failures across all streams with no 403s** — yt-dlp version
  rot. Pull/rebuild the image.
- **A stream in daily-cap dormancy** — the retry budget is spent; investigate
  the underlying cause rather than restarting the container to reset the
  counter.
- **Authenticated streams failing with valid-looking config** — check
  `cookies.txt` freshness; expired or stale cookies can also produce spurious
  "live event has ended" errors.

### Recovering from an IP ban

1. **Stop the detection containers.** Continued fetches extend the ban.
2. **Start `ops/ban-watch.sh`** on the host (in tmux). It polls one manifest
   resolve + one segment fetch every 30 minutes — two YouTube requests per
   poll, and the interval is deliberately conservative; do not lower it. It
   requires two consecutive 200s before declaring the ban lifted, then sends a
   high-priority ntfy notification with the restart command.
3. **Wait.** Bans age out. A modem power-cycle for a new DHCP lease is worth
   trying but often gets the same IP back.
4. **Restart the containers** only after ban-watch reports clear.

---

## Constraints for anyone deploying this

- **Rate sensitivity is structural.** Continuous 24/7 HLS fetches from a
  single IP will eventually trip anti-abuse systems. The backoff and daily
  cap keep the failure-mode request volume low, but the steady-state pattern
  is inherently scraper-shaped.
- **Network placement matters more than request shape.** Prefer
  datacenter/cloud egress over residential IPs. The options weighed for this
  deployment (#13): a cloud GPU instance, a split capture-in-cloud /
  detect-locally architecture, or routing only yt-dlp traffic through a
  datacenter VPN endpoint. Direct stream access from the camera operator —
  bypassing YouTube entirely — is the only complete fix.
- **You cannot fully impersonate a browser.** TLS fingerprinting means a
  Linux/OpenSSL client may be blocked where a real browser on the same IP
  with the same cookies is not.
- **Keep yt-dlp fresh.** Minimum `2024.12.01`; images upgrade it at build
  time. Treat sudden fleet-wide resolution failures as a version problem
  first.
- **Keep cookies fresh, or don't use them.** Public streams run cookie-less
  on purpose. If a stream needs authentication, the shared `cookies.txt`
  must be exported from a live browser session and refreshed when it goes
  stale.
- **No guaranteed uptime.** YouTube owes this system nothing. Every incident
  above happened without notice, and the next change will too. The
  architecture accepts gaps and keeps its records honest through them; plan
  operations the same way.

---

## Related

- #9 — bot-detection incident and JS-runtime fix
- #10 — IP-level 403 ban, evidence and analysis
- #11 — backoff system (commit `2bcf04c`)
- #13 — infrastructure relocation options
- `docker/DOCKER-DEPLOYMENT.md` — troubleshooting section
- `devlog/2026-07-16-tracking-rework.md` — stream-loss rates, outage handling
