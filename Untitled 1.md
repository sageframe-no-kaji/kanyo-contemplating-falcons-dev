**Yes!** Let's fix the thumbnails.

The problem: URLs are `/static/clips/...` but clips are at `/data/*/clips/...`

---

## The Fix

We need to add a route to serve clips from `/data/`. Let me check what we have:Now let me look at the actual clip_service.py code:I need to see the actual admin code. Let me check the transcript for context: