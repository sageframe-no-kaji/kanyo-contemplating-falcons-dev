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
- **COMPLETED** — **v1.0.0 production deploy executed on 2026-07-18 per `docs/deployment-kanyo.md`, plus a v1.0.1 hotfix.** All four detectors (harvard, nsw, fortwayne, umass) moved off the src-mount onto the pinned image; the new image-based compose template + `KANYO_IMAGE` pin are installed on the host; viewer and admin dashboard rebuilt to the release commit (`1a17265`); per-site tuning applied to harvard (`presence_sustain_confidence 0.10`, `merge_window_seconds 120`, `min_significant_seconds 15`, `damping_arrivals_threshold 10`) and fortwayne (`min_significant_seconds 60`, `damping_arrivals_threshold 6`). **Harvard canary surfaced a real breakage:** pure-live YouTube streams now require a **PO Token** solved by a **deno** JS challenge; the images shipped `node` (nsig solver) but not deno, so harvard failed to resolve on BOTH the old and 1.0.0 images (DVR streams like nsw/umass were unaffected). Fixed forward as **v1.0.1** — added pinned deno 2.9.3 to all three Dockerfiles (+node to cpu/vaapi for parity), no code change (PR #49). **The whole fleet now runs `1.0.1-nvidia`, `restarts=0`, all four streams resolving and detecting; harvard recovered.** Also removed the "Beta" brand badges from the viewer (kanyo-viewer `8b1e1ef`, deployed).
- **NEXT** — Monitor the fleet for a clean stretch (watch harvard's restart count stays 0 and clips write on the next arrivals). Decide when to merge **PR #47** (post-1.0 features: multi-bird count, chick-season docs, admin stream management, creature customization) — it is a separate open PR and was reviewed clean this session; it is NOT yet in production.
- **ACTION ITEMS / BLOCKS** — No blocks; Tailscale re-auth cleared, deploy done. Phase 0 backups live at `~/kanyo-v1.0.0-rollback/` on the host and in each site dir as `*.pre-v1.0.0` — remove after ~a week of clean operation. Doc drift to fix: `docs/deployment-kanyo.md` should note (a) the **deno/PO-token requirement** and that production is now `1.0.1-nvidia`, and (b) the real host mechanics — the deploy user is `atmarcus` (uid 1000, in `docker` group, **no passwordless sudo**), and `/opt/services/kanyo-admin` is **root-owned**, so the plan's `sudo`/`ssh -t`/`mv`/`sed -i` steps don't apply; use direct docker + `cat`-overwrite of the existing `.env`/compose files instead. Carried-forward follow-ups: yolov8s upgrade as a later image version once confidence-summary data accumulates (supersedes Agent Task 015); one-time host cleanup of stray `*.ffmpeg.log` files predating 027; viewer eslint debt untouched. Agent Tasks: 020 done; 013 (arrival clip anchoring) and 017 (multi-bird count) landed in PR #47 but not yet in production; 014, 016, 018, 019 remain open, priority unconfirmed.
- **PROJECT LIFECYCLE** — `production`

_Seeded 2026-07-09; updated 2026-07-16 at the close of the tracking-rework session; updated 2026-07-18 at the close of the v1.0.0 + v1.0.1 deploy._
