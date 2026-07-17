---
created: 2026-07-09
type: state-memory
project: kanyo
kamae: 6
status: living
---

# Kanyō — State Memory (Kamae 6)

This file is the build's living cross-session memory: hot, mutable, and non-canonical. It is read first by any fresh session to reconstitute build state quickly. The cold record — git history, per-ho Reflect sections in the devlog, and the build record — is always the source of truth; this file is a derived cache and yields to the cold record whenever they disagree.

---

**STATE-SUMMARY**
- **COMPLETED** — The 2026-07-16 tracking rework, end to end (full narrative: `devlog/2026-07-16-tracking-rework.md`): ho-10 event integrity & instrumentation (PRs #31–35), ho-11 stream reader thread + single time authority (#36–37), ho-12 presence layer (#38–40), ho-09 significance filter (#41–42), 026 admin today-visits timezone (#43), coverage campaign 46%→100% with the floor enforced at 95% (#44), image-based detector deployment route + v1.0.0 deploy plan in `docs/deployment-kanyo.md` (#45), and 027 ffmpeg-log leak fix (#46). Viewer repo aligned in parallel (kanyo-viewer PRs #4–7: microsecond filename compat, events-JSON authority, stream-timezone frontend, detector contract alignment). The v1.0.0 tag is imminent; CI publishes `1.0.0-nvidia` on tag push.
- **NEXT** — Execute `docs/deployment-kanyo.md` (tag v1.0.0 → canary Harvard → fleet) once Tailscale is re-authenticated.
- **ACTION ITEMS / BLOCKS** — **DEPLOY BLOCKED: Tailscale is logged out on the Mac. The host is unreachable until an interactive `tailscale` login is done. Nothing in the deploy plan can run before that.** Follow-ups after deploy: yolov8s upgrade as a separate later deploy, only after confidence-summary data accumulates (supersedes Agent Task 015); detector row enhancements (visit_clip_paths, provisional in-progress row, id microseconds, basename standardization); one-time host cleanup of stray `*.ffmpeg.log` files predating 027; admin dashboard image rebuild happens at deploy (Phase 2 of the plan); viewer carries 6 pre-existing eslint errors, untouched. Agent Tasks 013–019 status carried forward: 020 is done (roosting mode landed); 013 (arrival clip anchoring) and 017 (multi-bird count) were never executed; 015 is superseded by the deferred yolov8s plan; 014, 016, 018, 019 remain open with priority unconfirmed.
- **PROJECT LIFECYCLE** — `production`

_Seeded 2026-07-09 from git history and repo docs by a fleet pass; updated 2026-07-16 at the close of the tracking-rework session._
