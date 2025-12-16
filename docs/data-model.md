# Kanyo Data Model

## Detection Event
```json
{
  "timestamp": "2024-12-15T14:23:45Z",
  "youtube_time": "3h45m23s",
  "event_type": "falcon_enters",
  "confidence": 0.94,
  "thumbnail": "thumbs/20241215_142345.jpg",
  "falcon_count": 1,
  "bbox": [120, 340, 280, 520],
  "metadata": {
    "video_segment": "segment_20241215_1200.mp4",
    "frame_number": 12450
  }
}
```

## Event Types

- **falcon_enters** - Bird appears after absence (N frames)
- **falcon_exits** - Bird disappears after presence (N frames)
- **movement_after_stasis** - Movement after 5+ minutes still
- **falcon_count_change** - Number of visible falcons changes
- **significant_activity** - High motion detected

## Detection File

**File:** `site/data/detections.json`
```json
{
  "generated_at": "2024-12-15T15:00:00Z",
  "stream_url": "https://youtube.com/watch?v=...",
  "detection_config": {
    "model": "yolov8n.pt",
    "confidence": 0.6
  },
  "events": [
    { "event": "..." },
    { "event": "..." }
  ],
  "summary": {
    "total_events": 45,
    "falcon_enters": 12,
    "falcon_exits": 12,
    "movement_events": 21,
    "time_range": {
      "start": "2024-12-15T08:00:00Z",
      "end": "2024-12-15T15:00:00Z"
    }
  }
}
```

## Configuration Schema

See `config.yaml` - all settings documented inline.