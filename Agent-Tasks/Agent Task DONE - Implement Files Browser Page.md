# Agent Task: Implement Files Browser Page

## Goal
Make the "Files" link work — show a file browser for the stream's clips and logs directories.

## Route
`/streams/{stream_id}/files` or `/streams/{stream_id}/files/{path:path}`

## Features

1. **List directories and files** at `/data/{stream_id}/`
2. **Navigate** into subdirectories (clips/, logs/, clips/2025-12-30/)
3. **Click files** to:
   - Images: Show in media viewer
   - Videos: Play in media viewer  
   - Logs/text: Show content
   - Other: Download

## Implementation

### 1. Add route in `routers/pages.py`:
```python
@router.get("/streams/{stream_id}/files")
@router.get("/streams/{stream_id}/files/{path:path}")
async def stream_files(request: Request, stream_id: str, path: str = ""):
    """File browser for stream data."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    base_path = Path(f"/data/{stream_id}")
    current_path = base_path / path
    
    # Security: ensure path stays within stream directory
    try:
        current_path.resolve().relative_to(base_path.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if not current_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    
    # List directory contents
    items = []
    if current_path.is_dir():
        for item in sorted(current_path.iterdir()):
            items.append({
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else None,
                "modified": datetime.fromtimestamp(item.stat().st_mtime),
                "path": str(item.relative_to(base_path)),
            })
    
    # Breadcrumb parts
    parts = path.split("/") if path else []
    breadcrumbs = [{"name": "Root", "path": ""}]
    for i, part in enumerate(parts):
        if part:
            breadcrumbs.append({
                "name": part,
                "path": "/".join(parts[:i+1])
            })
    
    return templates.TemplateResponse("stream/files.html", {
        "request": request,
        "stream": stream,
        "items": items,
        "current_path": path,
        "breadcrumbs": breadcrumbs,
        "is_file": current_path.is_file(),
        "file_content": current_path.read_text() if current_path.is_file() and current_path.suffix in ['.log', '.txt', '.json', '.yaml'] else None,
    })
```

### 2. Create `templates/stream/files.html`:
```html
{% extends "base.html" %}

{% block title %}Files - {{ stream.name }}{% endblock %}

{% block content %}
<div class="container mx-auto px-4 py-8">
    <a href="/streams/{{ stream.id }}" class="text-zinc-400 hover:text-white mb-4 inline-block">
        ← Back to {{ stream.name }}
    </a>

    <h1 class="text-3xl font-bold mb-2">Files</h1>
    
    <!-- Breadcrumbs -->
    <nav class="flex items-center gap-2 text-sm text-zinc-400 mb-6">
        {% for crumb in breadcrumbs %}
            {% if not loop.last %}
                <a href="/streams/{{ stream.id }}/files/{{ crumb.path }}" 
                   class="hover:text-white">{{ crumb.name }}</a>
                <span>/</span>
            {% else %}
                <span class="text-white">{{ crumb.name }}</span>
            {% endif %}
        {% endfor %}
    </nav>

    <!-- File/Folder List -->
    <div class="bg-zinc-800 rounded-lg overflow-hidden">
        <table class="w-full">
            <thead class="bg-zinc-700 text-left text-sm">
                <tr>
                    <th class="px-4 py-2">Name</th>
                    <th class="px-4 py-2">Size</th>
                    <th class="px-4 py-2">Modified</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-zinc-700">
                {% if current_path %}
                <tr class="hover:bg-zinc-700">
                    <td class="px-4 py-2">
                        <a href="/streams/{{ stream.id }}/files/{{ '/'.join(current_path.split('/')[:-1]) }}"
                           class="flex items-center gap-2 text-blue-400 hover:text-blue-300">
                            📁 ..
                        </a>
                    </td>
                    <td></td>
                    <td></td>
                </tr>
                {% endif %}
                
                {% for item in items %}
                <tr class="hover:bg-zinc-700">
                    <td class="px-4 py-2">
                        {% if item.is_dir %}
                            <a href="/streams/{{ stream.id }}/files/{{ item.path }}"
                               class="flex items-center gap-2 text-blue-400 hover:text-blue-300">
                                📁 {{ item.name }}
                            </a>
                        {% elif item.name.endswith('.jpg') or item.name.endswith('.png') %}
                            <a href="#" 
                               onclick="showImage('/clips/{{ stream.id }}/{{ item.path }}', '{{ item.name }}')"
                               class="flex items-center gap-2 text-green-400 hover:text-green-300">
                                🖼️ {{ item.name }}
                            </a>
                        {% elif item.name.endswith('.mp4') %}
                            <a href="#"
                               onclick="playClip('/clips/{{ stream.id }}/{{ item.path }}', '{{ item.name }}')"
                               class="flex items-center gap-2 text-purple-400 hover:text-purple-300">
                                🎬 {{ item.name }}
                            </a>
                        {% else %}
                            <a href="/clips/{{ stream.id }}/{{ item.path }}" 
                               download
                               class="flex items-center gap-2 text-zinc-300 hover:text-white">
                                📄 {{ item.name }}
                            </a>
                        {% endif %}
                    </td>
                    <td class="px-4 py-2 text-sm text-zinc-400">
                        {% if item.size %}
                            {{ (item.size / 1024 / 1024) | round(1) }} MB
                        {% endif %}
                    </td>
                    <td class="px-4 py-2 text-sm text-zinc-400">
                        {{ item.modified.strftime('%Y-%m-%d %H:%M') }}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
```

### 3. Update the Files link in `templates/stream/detail.html`:

From:
```html
<a href="#" class="block bg-zinc-700 ...">📁 Files</a>
```

To:
```html
<a href="/streams/{{ stream.id }}/files" class="block bg-zinc-700 ...">📁 Files</a>
```

## Commit
```bash
git commit -m "feat: implement file browser for stream data

- Add /streams/{id}/files route with path navigation
- Directory listing with breadcrumbs
- Click images/videos to view in media player
- Security: path traversal protection"
```