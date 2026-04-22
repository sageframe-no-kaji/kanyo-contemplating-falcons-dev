# Event Search Tool

**Current Implementation:** Bash script for CLI log searching
**Future Goal:** Web UI integration for easy event investigation

## Current Implementation (Bash)

### Overview

`event-search.sh` is a command-line tool that searches kanyo logs by **stream-local time** with automatic timezone conversion and DST handling.

### Key Features

✅ **Auto-Discovery** - Automatically finds all kanyo streams under `/opt/services/`
✅ **Timezone-Aware** - Reads timezone from each stream's `config.yaml`
✅ **DST-Safe** - Uses system `date` command for proper daylight saving conversion
✅ **Midnight Crossing** - Handles search windows that span midnight
✅ **Smart Filtering** - Shows ERROR/INFO/WARNING/EVENT always, DEBUG only near EVENTs
✅ **Performance** - Caches UTC→local conversions for speed

### Usage

```bash
# List available streams
event-search --list-streams

# Search with today's date (stream timezone)
event-search nsw 20:33 10

# Search specific date
event-search harvard 2026-01-02 10:32 10

# Interactive mode
event-search
```

### Example Output

```
Searching stream 'nsw'
  Log file : /opt/services/kanyo-nsw/logs/kanyo.log
  Timezone : Australia/Sydney
  Window   : 2026-01-03 20:25 → 20:35

2026-01-03 09:30:15 UTC | EVENT | buffer_monitor | 🦅 FALCON ARRIVED...
2026-01-03 09:30:45 UTC | EVENT | buffer_monitor | ✅ Arrival confirmed...
```

### How It Works

1. **Discovery** - Scans `/opt/services/kanyo-*/` for config.yaml files
2. **Timezone Extraction** - Parses `timezone:` field from each config
3. **Time Conversion** - Converts user's local time to UTC for log searching
4. **Window Calculation** - Creates ±N minute window around target time
5. **Log Filtering** - AWK script reads log, converts timestamps, filters by window
6. **Caching** - Stores converted timestamps to avoid redundant `date` calls

### Architecture

```
User Input (Local Time)
    ↓
Convert to UTC (using stream timezone)
    ↓
Read kanyo.log (UTC timestamps)
    ↓
Convert to Local (for window matching)
    ↓
Filter by time window
    ↓
Return matching logs
```

## Future Development: Web UI Integration

### Phase 1: API Backend (1 hour)

**Add to `admin/web/app/services/log_service.py`:**

```python
def get_logs_in_window(
    stream_id: str,
    target_date: str,      # "2026-01-03"
    target_time: str,      # "20:33"
    window_minutes: int,   # 10
    show_context: bool = False,
) -> list[dict]:
    """
    Search logs in a time window (stream local time).

    Returns logs within ±window_minutes of target_time on target_date,
    accounting for stream timezone and DST.
    """
    from zoneinfo import ZoneInfo

    # Load stream timezone from config
    tz_name = _get_stream_timezone(stream_id)
    tz = ZoneInfo(tz_name)

    # Parse target time in stream's local timezone
    local_dt = datetime.strptime(f"{target_date} {target_time}", "%Y-%m-%d %H:%M")
    local_dt = local_dt.replace(tzinfo=tz)

    # Convert to UTC
    utc_dt = local_dt.astimezone(timezone.utc)

    # Calculate window bounds
    half = timedelta(minutes=window_minutes // 2)
    start_utc = utc_dt - half
    end_utc = utc_dt + half

    # Read logs from file
    log_path = Path(f"/data/{stream_id}/logs/kanyo.log")
    matching_logs = []

    with open(log_path) as f:
        for line in f:
            parsed = _parse_log_line(line)
            if parsed and start_utc <= parsed["timestamp"] <= end_utc:
                matching_logs.append(parsed)

    # Apply EVENT context if requested
    if show_context:
        matching_logs = _add_event_context(matching_logs)

    return matching_logs

def _get_stream_timezone(stream_id: str) -> str:
    """Read timezone from stream's config.yaml."""
    from app.services.config_service import get_stream_config
    config = get_stream_config(stream_id)
    return config.get("timezone", "UTC")
```

**Add to `admin/web/app/routers/api.py`:**

```python
@router.get("/api/streams/{stream_id}/logs/search")
def search_logs_by_time(
    stream_id: str,
    date: str = Query(..., description="YYYY-MM-DD"),
    time: str = Query(..., description="HH:MM"),
    window: int = Query(10, ge=1, le=60, description="Window in minutes"),
    context: bool = Query(False, description="Show DEBUG context around EVENTs"),
):
    """Search logs by local time window."""
    logs = log_service.get_logs_in_window(
        stream_id=stream_id,
        target_date=date,
        target_time=time,
        window_minutes=window,
        show_context=context,
    )
    return {"logs": logs, "count": len(logs)}
```

### Phase 2: Frontend UI (1 hour)

**Option A: Add to Logs Page**

Add search form above existing filters in `logs.html`:

```html
<!-- Event Search Section -->
<div class="event-search-panel">
    <h3>🔍 Event Search</h3>
    <p class="help-text">Search logs by stream-local time ({{timezone}})</p>

    <div class="search-form">
        <div class="form-group">
            <label>Date</label>
            <input type="date" id="search-date"
                   value="{{ today }}" />
        </div>

        <div class="form-group">
            <label>Time</label>
            <input type="time" id="search-time"
                   value="{{ now }}" />
        </div>

        <div class="form-group">
            <label>Window (minutes)</label>
            <input type="number" id="search-window"
                   value="10" min="1" max="60" />
        </div>

        <button class="btn btn-primary" onclick="searchEventWindow()">
            Search Window
        </button>

        <button class="btn btn-secondary" onclick="clearEventSearch()">
            Clear
        </button>
    </div>
</div>

<!-- Separator -->
<hr class="section-divider">

<!-- Existing filters -->
<div class="log-filters">
    ...
</div>
```

**JavaScript:**

```javascript
async function searchEventWindow() {
    const date = document.getElementById('search-date').value;
    const time = document.getElementById('search-time').value;
    const window = document.getElementById('search-window').value;

    // Validate inputs
    if (!date || !time) {
        alert('Please select both date and time');
        return;
    }

    // Call API
    const response = await fetch(
        `/api/streams/${streamId}/logs/search?` +
        `date=${date}&time=${time}&window=${window}&context=true`
    );

    const data = await response.json();

    // Display results
    displayLogs(data.logs);

    // Show info banner
    showSearchInfo(date, time, window, data.count);
}

function showSearchInfo(date, time, window, count) {
    const banner = document.createElement('div');
    banner.className = 'search-info-banner';
    banner.innerHTML = `
        📍 Showing ${count} logs from
        ${date} ${time} ±${window} minutes
        <button onclick="clearEventSearch()">✕</button>
    `;
    document.querySelector('.log-container').prepend(banner);
}
```

**Option B: Dedicated Tools Page**

Create new page at `/streams/{id}/tools`:

```html
<!-- tools.html -->
<div class="tools-page">
    <h1>🛠️ Stream Tools</h1>

    <!-- Event Search Tool -->
    <div class="tool-card">
        <h2>🔍 Event Search</h2>
        <p>Search logs by precise time windows in stream-local timezone</p>
        <!-- Same search form as above -->
    </div>

    <!-- Future Tools -->
    <div class="tool-card disabled">
        <h2>📝 Manual Clip Creation</h2>
        <p>Create clips from visit recordings (coming soon)</p>
    </div>

    <div class="tool-card disabled">
        <h2>⚙️ Config Editor</h2>
        <p>Edit stream configuration (coming soon)</p>
    </div>
</div>
```

### Phase 3: Advanced Features (Future)

**1. Preset Time Ranges**
```html
<div class="quick-search">
    <button onclick="searchLastHour()">Last Hour</button>
    <button onclick="searchToday()">Today</button>
    <button onclick="searchYesterday()">Yesterday</button>
</div>
```

**2. Event Timeline View**
```javascript
// Show events on a visual timeline
function renderTimeline(logs) {
    const events = logs.filter(l => l.level === 'EVENT');
    // D3.js or similar for timeline visualization
}
```

**3. Export Results**
```javascript
function exportSearchResults(logs) {
    const csv = logsToCSV(logs);
    downloadFile(csv, `event-search-${date}-${time}.csv`);
}
```

**4. Saved Searches**
```javascript
// Save frequently used search patterns
localStorage.setItem('savedSearches', JSON.stringify([
    { name: "Evening Arrivals", time: "18:00", window: 30 },
    { name: "Morning Departures", time: "08:00", window: 30 },
]));
```

**5. Multi-Stream Search**
```html
<div class="multi-stream-search">
    <label>
        <input type="checkbox" value="nsw"> NSW
    </label>
    <label>
        <input type="checkbox" value="harvard"> Harvard
    </label>
    <button onclick="searchAllStreams()">Search All</button>
</div>
```

## Migration Plan

### Step 1: Keep Bash Script (Current)
- Continue using CLI for quick investigations
- Script runs on server, accessible via SSH
- No dependency on web UI being available

### Step 2: Add Web UI (Phase 1-2)
- Implement API endpoint
- Add search form to Logs page
- Both CLI and Web UI work independently
- Web UI more convenient, CLI faster for power users

### Step 3: Advanced Features (Phase 3)
- Add timeline view
- Export functionality
- Saved searches
- Multi-stream support

### Step 4: Sunset CLI (Optional)
- If web UI fully mature and preferred
- Keep script in repo for emergency access
- Document CLI in "Advanced Tools" section

## Technical Considerations

### Timezone Handling
**Challenge:** Python `zoneinfo` vs bash `TZ=` env var
**Solution:** Both use IANA timezone database, should be consistent

**Test Cases:**
- DST transition days (spring forward, fall back)
- Midnight crossing searches
- Leap seconds (rare, but possible)

### Performance
**Current (Bash):**
- Reads entire log file
- ~1-2 seconds for 100MB log
- Caching helps for multiple searches

**Web UI:**
- Same approach initially
- Future: Index logs by timestamp for faster searches
- Consider log rotation (old logs compressed)

### Security
**Considerations:**
- Validate date/time inputs (prevent injection)
- Rate limit API endpoint (prevent abuse)
- Require authentication (already in place)

### Midnight Crossing Bug
**Current Bash Implementation:**
```bash
# If window crosses midnight
if (( START_MIN < 0 )); then
    # Search previous day + current day
fi
```

**Web UI Should:**
```python
# Handle searches like "23:50 ±20 minutes"
# Should search 23:40 on date AND 00:10 on date+1
if end_time < start_time:  # Crossed midnight
    # Search two date ranges
    logs1 = search_range(date, start_time, "23:59:59")
    logs2 = search_range(date + 1day, "00:00:00", end_time)
    return logs1 + logs2
```

## Usage Patterns

### Debugging False Arrivals
**Workflow:**
1. See notification: "Falcon arrived at 8:33 PM"
2. Open Logs page
3. Event Search: `2026-01-03 20:33 ±10 min`
4. Check if arrival was confirmed or cancelled
5. Examine DEBUG logs around event

### Investigating Missed Departures
**Workflow:**
1. Notice bird left but no departure notification
2. Estimate departure time from stream
3. Event Search around that time
4. Look for timeout logs, state machine transitions
5. Check if recording stopped properly

### Performance Analysis
**Workflow:**
1. User reports "slow detections"
2. Search during problem time window
3. Look for detection interval logs
4. Check frame processing times
5. Identify bottlenecks

## Related Scripts

- `event-search.sh` - Current CLI implementation
- `update-admin.sh` - Deploy web UI changes
- Future: `log-analyzer.sh` - Batch analysis of log patterns

## Documentation Links

- [Log Format](../docs/architecture.md#logging)
- [Timezone Configuration](../configs/README.md#timezone)
- [Admin UI Development](../admin/README.md)

## Credits

**Original Bash Implementation:** Manual timezone mapping, basic search
**Auto-Discovery Version:** Stream discovery, config-based timezones
**Future Web UI:** Planned integration with admin panel
