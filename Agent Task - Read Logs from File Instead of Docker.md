# Agent Task: Read Logs from File Instead of Docker

## Goal
Switch from `docker logs` to reading `/data/{stream}/logs/kanyo.log` so logs persist across restarts and can be viewed historically.

## Changes

### 1. Update `services/docker_service.py` (or create `log_service.py`):
```python
from pathlib import Path
from datetime import datetime, timedelta

def get_logs(stream_id: str, since: str = "startup", lines: int = 500) -> list[dict]:
    """
    Read logs from kanyo.log file.
    
    Args:
        stream_id: Stream identifier
        since: "startup", "1h", "24h", "7d", "all"
        lines: Max lines to return
    
    Returns:
        List of log line dicts with timestamp, level, message
    """
    log_path = Path(f"/data/{stream_id}/logs/kanyo.log")
    
    if not log_path.exists():
        return []
    
    # Calculate cutoff time
    cutoff = None
    if since == "1h":
        cutoff = datetime.now() - timedelta(hours=1)
    elif since == "24h":
        cutoff = datetime.now() - timedelta(days=1)
    elif since == "7d":
        cutoff = datetime.now() - timedelta(days=7)
    elif since == "startup":
        # Find last startup message
        cutoff = _find_last_startup(log_path)
    # "all" = no cutoff
    
    # Read and filter
    log_lines = []
    with open(log_path, 'r') as f:
        for line in f:
            parsed = _parse_log_line(line)
            if parsed:
                if cutoff is None or parsed["timestamp"] >= cutoff:
                    log_lines.append(parsed)
    
    # Return last N lines
    return log_lines[-lines:]


def _find_last_startup(log_path: Path) -> datetime:
    """Find timestamp of last 'BUFFER-BASED FALCON MONITOR' line."""
    last_startup = None
    with open(log_path, 'r') as f:
        for line in f:
            if "BUFFER-BASED FALCON MONITOR" in line:
                parsed = _parse_log_line(line)
                if parsed:
                    last_startup = parsed["timestamp"]
    return last_startup or datetime.min


def _parse_log_line(line: str) -> dict | None:
    """Parse log line into structured dict."""
    # Format: 2025-12-30 12:08:03 | INFO     | module | message
    try:
        parts = line.split(" | ", 3)
        if len(parts) >= 4:
            timestamp = datetime.strptime(parts[0].strip(), "%Y-%m-%d %H:%M:%S")
            return {
                "timestamp": timestamp,
                "level": parts[1].strip(),
                "module": parts[2].strip(),
                "message": parts[3].strip(),
                "raw": line.strip(),
            }
    except:
        pass
    return None
```

### 2. Update `routers/api.py`:
```python
@router.get("/streams/{stream_id}/logs")
async def get_stream_logs(
    stream_id: str, 
    since: str = "startup",
    lines: int = 500,
    levels: str = "INFO,EVENT,WARNING,ERROR"  # Comma-separated
):
    logs = log_service.get_logs(stream_id, since=since, lines=lines)
    
    # Filter by levels
    selected_levels = levels.split(",")
    logs = [l for l in logs if l["level"] in selected_levels]
    
    return {"logs": logs}
```

### 3. Update `templates/stream/logs.html`:

Add time range dropdown:
```html
<div class="flex items-center gap-4 mb-4">
    <div class="flex items-center gap-2">
        <span class="text-sm text-zinc-400">Time:</span>
        <select id="log-since" 
                onchange="refreshLogs()"
                class="bg-zinc-700 rounded px-3 py-1 text-sm">
            <option value="startup">Since startup</option>
            <option value="1h">Last hour</option>
            <option value="24h">Last 24 hours</option>
            <option value="7d">Last 7 days</option>
            <option value="all">All time</option>
        </select>
    </div>
    
    <!-- Level tags here -->
</div>
```

### 4. Update JS to fetch with params:
```javascript
async function refreshLogs() {
    const since = document.getElementById('log-since').value;
    const levels = getSelectedLevels().join(',');
    
    const response = await fetch(`/api/streams/${streamId}/logs?since=${since}&levels=${levels}`);
    const data = await response.json();
    
    renderLogs(data.logs);
}
```

## Log Rotation (Optional)

Add to config or hardcode: keep last 10MB or 7 days. But that's a separate task.

## Commit
```bash
git commit -m "feat: read logs from file with time range filter

- Switch from docker logs to kanyo.log file
- Add time range: startup, 1h, 24h, 7d, all
- Logs persist across container restarts
- Parse log lines for level filtering"
```