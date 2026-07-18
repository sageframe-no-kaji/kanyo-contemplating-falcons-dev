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
- **COMPLETED** — **Everything current, merged, and deployed as of 2026-07-18.** Sequence: v1.0.0 image migration (all four detectors off the src-mount onto pinned images) → **v1.0.1** deno hotfix (harvard went pure-live; YouTube now gates pure-live extraction behind a **PO Token** solved by a **deno** JS challenge — images had `node`/nsig but not deno, so harvard failed on BOTH old and 1.0.0 images while DVR streams nsw/umass were fine; fixed by adding pinned deno 2.9.3 to all three Dockerfiles, PR #49) → **PR #47 merged** (post-1.0 features: multi-bird count `BirdCountTracker` off by default, chick-season docs preset, FalconVisit row lifecycle, admin stream auto-discovery + Create-Stream form + compose-snippet gen, creature customization, YouTube constraints doc) → **v1.1.0 tagged and deployed**. **The whole fleet now runs `1.1.0-nvidia` (#47 + deno), `restarts=0`, all four resolving and detecting.** Admin dashboard rebuilt on #47 code and switched to the single `/opt/services:/data` parent mount with stream auto-discovery (reserved dirs excluded). Viewer also hit the same PO-token gap in its server-side live-URL resolver (`streams.py`) — fixed by adding deno to the viewer image (kanyo-viewer `95dd1ee`), and "Beta" brand badges removed (`8b1e1ef`); viewer live player recovered. Per-site tuning live: harvard (`presence_sustain_confidence 0.10`, `merge_window_seconds 120`, `min_significant_seconds 15`, `damping_arrivals_threshold 10`), fortwayne (`min_significant_seconds 60`, `damping_arrivals_threshold 6`). Issues closed: #5, #6, #8 (delivered by #47), #14 (auto). Filed **#50** to track the deno/PO-token work + doc follow-ups.
- **NEXT** — Monitor for a clean stretch (restart counts stay 0, clips write on arrivals). Optional next moves, all their own decisions: enable `bird_count_enabled` per-site once you want counts live (currently off everywhere); field-validate bird-count + chick-season against real breeding footage (issues #3, #1, #2); build live container orchestration (#7); pursue direct-stream partnerships (#15).
- **ACTION ITEMS / BLOCKS** — No blocks. **Doc drift (tracked in #50):** `docs/deployment-kanyo.md` still says `1.0.0-nvidia` (prod is now `1.1.0-nvidia`) and assumes `sudo`/`ssh -t`/`mv`/`sed -i` — the real host mechanics are: deploy user `atmarcus` (uid 1000, `docker` group, **no passwordless sudo**), `/opt/services/kanyo-admin` **root-owned** (can overwrite existing files but not create new ones → use `cat`-overwrite, not `sed -i`/`mv`); backups went to `~/kanyo-v1.0.0-rollback/`. Fold the deno/PO-token finding into `docs/youtube-stream-constraints.md`. Policy: any image resolving YouTube live via yt-dlp must include deno (detector + viewer both do now). Phase 0 backups (`~/kanyo-v1.0.0-rollback/` + `*.pre-v1.0.0` in site dirs) — remove after ~a week clean. Carried forward: yolov8s upgrade as a later image once confidence-summary data accumulates (supersedes Agent Task 015); one-time host cleanup of stray `*.ffmpeg.log` predating 027; viewer eslint debt. Agent Tasks 013 + 017 landed via #47 and are now in production; 014, 016, 018, 019 open, priority unconfirmed.
- **PROJECT LIFECYCLE** — `production`

_Seeded 2026-07-09; updated 2026-07-16 (tracking-rework close); updated 2026-07-18 at the close of the v1.0.0 → v1.0.1 → #47/v1.1.0 deploy (full fleet on 1.1.0-nvidia, everything merged and deployed)._
