# Agent Task: Hide In-Progress Recordings

## Problem
Visit clips (and arrival clips) appear in the admin GUI while still being recorded. They're unplayable and can stay that way for hours.

## Solution
Use `.tmp` extension while recording, rename to `.mp4` when complete.

## Changes

### In `src/kanyo/utils/visit_recorder.py`:

When starting recording:
```python
# Instead of:
self.output_path = clips_dir / f"falcon_{time_str}_visit.mp4"

# Use:
self.output_path = clips_dir / f"falcon_{time_str}_visit.mp4.tmp"
self.final_path = clips_dir / f"falcon_{time_str}_visit.mp4"
```

When recording completes:
```python
# After closing the video writer:
self.output_path.rename(self.final_path)
logger.event(f"✅ Visit recording complete: {self.final_path}")
```

### Same pattern for:
- `src/kanyo/utils/arrival_clip_recorder.py`
- Any other recorder that writes incrementally

### Admin GUI
No changes needed — it already globs for `*.mp4`, so `.tmp` files are automatically excluded.

## Commit
```bash
git commit -m "fix: use .tmp extension for in-progress recordings

Recordings now write to .mp4.tmp and rename to .mp4 on completion.
This prevents incomplete files from appearing in the admin GUI."
```