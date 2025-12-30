# Agent Task: Change Log Level Filter to Multi-Select Tags

## Goal
Replace the level dropdown with clickable tag-style checkboxes so users can show/hide multiple levels at once.

## Current
```html
<select>
  <option>All</option>
  <option>INFO</option>
  <option>WARNING</option>
  <option>ERROR</option>
</select>
```

## New Design
```
Level: [DEBUG] [INFO] [EVENT] [WARNING] [ERROR]
         ○       ●      ●        ●         ●
```

Clickable pills/tags. Filled = shown, empty = hidden. Multiple can be selected.

## Implementation

### Update `templates/stream/logs.html`:
```html
<div class="flex items-center gap-2">
    <span class="text-sm text-zinc-400">Level:</span>
    
    <label class="cursor-pointer">
        <input type="checkbox" name="log-level" value="DEBUG" class="hidden peer">
        <span class="px-3 py-1 rounded-full text-xs font-medium 
                     bg-zinc-700 text-zinc-400
                     peer-checked:bg-zinc-500 peer-checked:text-white
                     hover:bg-zinc-600 transition">
            DEBUG
        </span>
    </label>
    
    <label class="cursor-pointer">
        <input type="checkbox" name="log-level" value="INFO" checked class="hidden peer">
        <span class="px-3 py-1 rounded-full text-xs font-medium 
                     bg-zinc-700 text-zinc-400
                     peer-checked:bg-blue-600 peer-checked:text-white
                     hover:bg-zinc-600 transition">
            INFO
        </span>
    </label>
    
    <label class="cursor-pointer">
        <input type="checkbox" name="log-level" value="EVENT" checked class="hidden peer">
        <span class="px-3 py-1 rounded-full text-xs font-medium 
                     bg-zinc-700 text-zinc-400
                     peer-checked:bg-green-600 peer-checked:text-white
                     hover:bg-zinc-600 transition">
            EVENT
        </span>
    </label>
    
    <label class="cursor-pointer">
        <input type="checkbox" name="log-level" value="WARNING" checked class="hidden peer">
        <span class="px-3 py-1 rounded-full text-xs font-medium 
                     bg-zinc-700 text-zinc-400
                     peer-checked:bg-amber-600 peer-checked:text-white
                     hover:bg-zinc-600 transition">
            WARNING
        </span>
    </label>
    
    <label class="cursor-pointer">
        <input type="checkbox" name="log-level" value="ERROR" checked class="hidden peer">
        <span class="px-3 py-1 rounded-full text-xs font-medium 
                     bg-zinc-700 text-zinc-400
                     peer-checked:bg-red-600 peer-checked:text-white
                     hover:bg-zinc-600 transition">
            ERROR
        </span>
    </label>
</div>
```

### Update `static/js/app.js`:
```javascript
// Log filtering by multiple levels
function getSelectedLevels() {
    const checkboxes = document.querySelectorAll('input[name="log-level"]:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

function filterLogs() {
    const selectedLevels = getSelectedLevels();
    const logLines = document.querySelectorAll('.log-line');
    
    logLines.forEach(line => {
        const level = line.dataset.level;  // Add data-level to log lines
        if (selectedLevels.length === 0 || selectedLevels.includes(level)) {
            line.classList.remove('hidden');
        } else {
            line.classList.add('hidden');
        }
    });
}

// Attach to checkboxes
document.querySelectorAll('input[name="log-level"]').forEach(cb => {
    cb.addEventListener('change', filterLogs);
});
```

### Update log line rendering to include data attribute:
```html
<div class="log-line" data-level="INFO">
    2025-12-30 12:08:03 | INFO | ...
</div>
```

## Default State
- DEBUG: unchecked (off by default)
- INFO, EVENT, WARNING, ERROR: checked (on by default)

## Commit
```bash
git commit -m "feat: multi-select log level filter tags

- Replace dropdown with clickable tag checkboxes
- Color-coded by level (blue=INFO, green=EVENT, amber=WARNING, red=ERROR)
- Multiple levels can be shown simultaneously
- DEBUG hidden by default"
```