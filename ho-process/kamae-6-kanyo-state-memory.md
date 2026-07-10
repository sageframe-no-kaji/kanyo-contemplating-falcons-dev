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
- **COMPLETED** — The bulk of the `021` sprint: a sequence of agent-task-driven fixes across admin timezone handling (021-G), arrival confirmation recording actual last detection time (021-J), buffer monitor startup / frame interval wiring (021-D), .mp4.tmp finalization in roosting-stop mode (021-E), stream data root unification (021-C), HTML/URL escaping in admin API handlers (021-H), admin restart/stop/start TypeError on thumbnail (021-B), EventMetadata/StateEvent type aliases bringing mypy to clean (021-K), pytest import without manual PYTHONPATH (021-A), CI build-cpu tag template fix (5557794), and output filename microsecond collision fix (021-I, commit a014025 — most recent merged). The ho-09 significance-filter doc was scaffolded (1e69053) but the ho is not yet authored or executed.
- **NEXT** — Author and execute ho-09 (significance filter / event merging). The scaffold and motivating framing are in `devlog/ho-09-significance-filter.md`. Several Agent Tasks (013–020) also appear open in `devlog/Agent-Tasks/` and may be sequenced before or alongside ho-09; the practitioner should confirm priority on the next session.
- **ACTION ITEMS / BLOCKS** — Agent Tasks 013–020 in `devlog/Agent-Tasks/` show no "DONE" suffix and appear open (Fix Arrival Clip Anchoring; Add Public Recent Changes Section; Upgrade YOLO Model; Generate Daily Timelapses; Multi-Bird Count Tracking; Clip Flagging / Best Of Page; Clip Retention and Cleanup; Roosting Mode and Documentation Update). Status of these tasks is uncertain from git history alone — practitioner to verify which are still active vs. superseded. No build blockers identified from evidence.
- **PROJECT LIFECYCLE** — `production`

_Seeded 2026-07-09 from git history and repo docs by a fleet pass; verify on next session._
