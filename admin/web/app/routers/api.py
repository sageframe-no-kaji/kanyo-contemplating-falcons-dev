"""API router for JSON endpoints."""

from fastapi import APIRouter, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.services import (
    stream_service,
    docker_service,
    config_service,
    clip_service,
    log_service,
    file_service,
    system_service,
)
from app.services.stream_manager import create_stream, restart_admin_container, validate_stream_id


router = APIRouter()

# Set up templates for HTML fragments
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/streams")
async def list_streams():
    """List all streams with status."""
    streams = stream_service.discover_streams()

    # Add status info to each stream
    for stream in streams:
        status = docker_service.get_container_status(stream["container_name"])
        stream["status"] = status["status"]
        stream["uptime"] = status.get("uptime", "")

    return {"streams": streams}


@router.get("/streams/{stream_id}")
async def get_stream(stream_id: str):
    """Get single stream detail."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Add status info
    status = docker_service.get_container_status(stream["container_name"])
    stream["status"] = status["status"]
    stream["uptime"] = status.get("uptime", "")

    return stream


@router.post("/streams/{stream_id}/restart")
async def restart_stream(stream_id: str):
    """Restart container and return HTML fragment."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    success = docker_service.restart_container(stream["container_name"])

    if not success:
        raise HTTPException(status_code=500, detail="Failed to restart container")

    # Return updated stream card HTML fragment
    from app.services import clip_service

    # Get updated status
    status = docker_service.get_container_status(stream["container_name"])
    stream["status"] = status["status"]
    stream["uptime"] = status.get("uptime", "")

    # Get clip info
    stream["latest_thumbnail"] = clip_service.get_latest_thumbnail(stream["clips_path"])
    stream["today_visits"] = clip_service.get_today_visits(stream["clips_path"])
    stream["last_event"] = clip_service.get_last_event(stream["clips_path"])

    return templates.TemplateResponse(
        "components/stream_card.html", {"request": {}, "stream": stream}, media_type="text/html"
    )


@router.post("/streams/{stream_id}/stop")
async def stop_stream(stream_id: str):
    """Stop container and return HTML fragment."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    success = docker_service.stop_container(stream["container_name"])

    if not success:
        raise HTTPException(status_code=500, detail="Failed to stop container")

    # Return updated stream card HTML fragment
    from app.services import clip_service

    # Get updated status
    status = docker_service.get_container_status(stream["container_name"])
    stream["status"] = status["status"]
    stream["uptime"] = status.get("uptime", "")

    # Get clip info
    stream["latest_thumbnail"] = clip_service.get_latest_thumbnail(stream["clips_path"])
    stream["today_visits"] = clip_service.get_today_visits(stream["clips_path"])
    stream["last_event"] = clip_service.get_last_event(stream["clips_path"])

    return templates.TemplateResponse(
        "components/stream_card.html", {"request": {}, "stream": stream}, media_type="text/html"
    )


@router.post("/streams/{stream_id}/start")
async def start_stream(stream_id: str):
    """Start container and return HTML fragment."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    success = docker_service.start_container(stream["container_name"])

    if not success:
        raise HTTPException(status_code=500, detail="Failed to start container")

    # Return updated stream card HTML fragment
    from app.services import clip_service

    # Get updated status
    status = docker_service.get_container_status(stream["container_name"])
    stream["status"] = status["status"]
    stream["uptime"] = status.get("uptime", "")

    # Get clip info
    stream["latest_thumbnail"] = clip_service.get_latest_thumbnail(stream["clips_path"])
    stream["today_visits"] = clip_service.get_today_visits(stream["clips_path"])
    stream["last_event"] = clip_service.get_last_event(stream["clips_path"])

    return templates.TemplateResponse(
        "components/stream_card.html", {"request": {}, "stream": stream}, media_type="text/html"
    )


@router.get("/streams/{stream_id}/clips", response_class=HTMLResponse)
async def get_clips_for_date(request: Request, stream_id: str, hours: int = 24):
    """Get clips from the last N hours (relative time, not calendar days)."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Get clips from last N hours using stream's timezone
    stream_tz = stream.get("timezone", "UTC")
    clips = clip_service.list_clips_since(stream["clips_path"], stream_tz, hours)

    # Filter to only show videos (skip still images)
    clips = [c for c in clips if c["is_video"]]

    # Render clips grid HTML
    if not clips:
        return '<p class="text-zinc-400">No clips in the last ' + str(hours) + " hours</p>"

    # Render the clips grid (same as detail page)
    html = """
    <!-- Legend -->
    <div class="flex gap-4 mb-4 text-xs">
        <span class="flex items-center gap-1">
            <span class="w-3 h-3 rounded bg-green-600"></span> Arrival
        </span>
        <span class="flex items-center gap-1">
            <span class="w-3 h-3 rounded bg-red-600"></span> Departure
        </span>
        <span class="flex items-center gap-1">
            <span class="w-3 h-3 rounded bg-blue-600"></span> Visit
        </span>
    </div>

    <div class="grid grid-cols-3 gap-3">
    """

    for clip in clips[:12]:
        thumb_name = clip["filename"].rsplit(".", 1)[0] + ".jpg"
        date_str = clip["date"]
        clip_type_color = {
            "arrival": "bg-green-600",
            "departure": "bg-red-600",
            "visit": "bg-blue-600",
        }.get(clip["type"], "bg-zinc-600")

        html += f"""
        <div class="aspect-video bg-zinc-900 rounded overflow-hidden relative group cursor-pointer"
             onclick="playClip('/clips/{stream_id}/{date_str}/{clip['filename']}', \
'{clip['type']} at {clip['time']}')">
            <img src="/clips/{stream_id}/{date_str}/{thumb_name}"
                 class="w-full h-full object-cover"
                 onerror="this.style.display='none'"
                 alt="{clip['type']} at {clip['time']}">
            <div class="absolute bottom-0 left-0 right-0 \
bg-gradient-to-t from-black/80 to-transparent p-2">
                <div class="flex flex-col gap-1">
                    <div class="flex items-center justify-between text-xs">
                        <span class="flex items-center gap-1">
                            <span class="text-[10px] font-bold bg-white/20 px-1 rounded">\
VID</span>
                        </span>
                        <span class="px-1.5 py-0.5 rounded text-[10px] font-medium \
{clip_type_color}">
                            {clip['type']}
                        </span>
                    </div>
                    <div class="text-[11px] text-white/90">
                        {date_str} {clip['time']}
                    </div>
                </div>
            </div>
            <div class="absolute inset-0 flex items-center justify-center opacity-0 \
group-hover:opacity-100 transition bg-black/30">
                <span class="text-4xl">▶</span>
            </div>
        </div>
        """

    html += "</div>"
    return html


@router.get("/streams/{stream_id}/events", response_class=HTMLResponse)
async def get_events_for_range(request: Request, stream_id: str, hours: int = 24):
    """Get events from the last N hours."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Get events from last N hours using stream's timezone
    stream_tz = stream.get("timezone", "UTC")
    events = clip_service.get_recent_events(stream["clips_path"], stream_tz, hours)

    # Render events HTML
    if not events:
        # Calculate label
        if hours == 24:
            label = "24 hours"
        else:
            days = hours // 24
            label = f"{days} days"
        return f'<p class="text-zinc-500 text-sm">No events in the last {label}</p>'

    html = '<div class="space-y-2 max-h-96 overflow-y-auto">'
    for event in events:
        html += f"""
    <div class="text-sm hover:bg-zinc-700 rounded px-2 py-1 -mx-2 cursor-pointer transition"
         onclick="playClip('/clips/{stream_id}/{event['date']}/{event['filename']}', '{event['type']} at {event['time']}')">
        <span class="text-zinc-400">
            {event['date']} {event['time']}
            <span class="event-local-time text-zinc-500" data-datetime="{event['datetime'].isoformat()}"></span>
        </span>
        <span class="text-white ml-2">{event['type']}</span>
    </div>"""
    html += "</div>"
    return html


@router.get("/streams/{stream_id}/logs")
async def get_logs(
    stream_id: str,
    since: str = "startup",
    lines: int = 500,
    levels: str = "INFO,EVENT,WARNING,ERROR",
    show_context: bool = False,
):
    """
    Get logs from kanyo.log file with filtering.

    Args:
        stream_id: Stream identifier
        since: Time range - "startup", "1h", "8h", "24h", "3d", "7d"
        lines: Maximum number of lines to return
        levels: Comma-separated log levels to include
        show_context: If True, show DEBUG lines within ±5 lines of EVENT logs

    Returns:
        HTML with log lines showing timestamps in stream's local timezone
    """
    from zoneinfo import ZoneInfo

    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Get stream's timezone for display
    stream_tz_name = stream.get("timezone", "UTC")
    try:
        stream_tz = ZoneInfo(stream_tz_name)
    except Exception:
        stream_tz = ZoneInfo("UTC")

    # Parse levels parameter
    level_list = [level.strip() for level in levels.split(",") if level.strip()] if levels else None

    # Get logs from file (timestamps are UTC-aware)
    logs = log_service.get_logs(
        stream_id, since=since, lines=lines, levels=level_list, show_context=show_context
    )

    # Format as HTML with data attributes, converting timestamps to stream local time
    html_lines = []
    for log_entry in logs:
        level = log_entry["level"]
        timestamp_utc = log_entry["timestamp"]

        # Convert UTC timestamp to stream's local timezone
        timestamp_local = timestamp_utc.astimezone(stream_tz)

        # Format as: 2025-12-30 12:08:03 (stream local) | LEVEL | module | message
        timestamp_str = timestamp_local.strftime("%Y-%m-%d %H:%M:%S")

        # Wrap level in span for highlighting
        level_padded = f"{level:<8}"
        formatted_line = (
            f"{timestamp_str} (stream local) | "
            f'<span class="log-level">{level_padded}</span> | '
            f'{log_entry["module"]} | {log_entry["message"]}'
        )

        html_lines.append(f'<div class="log-line" data-level="{level}">{formatted_line}</div>')

    return HTMLResponse("\n".join(html_lines))


@router.put("/streams/{stream_id}/config")
@router.post("/streams/{stream_id}/config")
async def update_config(
    request: Request,
    stream_id: str,
    action: str = Form("save"),
    stream_name: str = Form(...),
    video_source: str = Form(...),
    detection_confidence: float = Form(...),
    detection_confidence_ir: str = Form(""),
    frame_interval: int = Form(...),
    timezone: str = Form(...),
    exit_timeout: int = Form(...),
    roosting_threshold: int = Form(...),
    telegram_enabled: bool = Form(False),
    telegram_channel: str = Form(""),
):
    """Update stream configuration."""
    try:
        # Get stream info
        stream = stream_service.get_stream(stream_id)
        if not stream:
            return HTMLResponse(
                f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
                f"Error: Stream {stream_id} not found"
                f"</div>",
                status_code=404,
            )

        # Read existing config to preserve fields not in the form
        existing_config = config_service.read_config(stream["config_path"])

        # Update only the fields from the form
        updated_fields = {
            "stream_name": stream_name,
            "video_source": video_source,
            "detection_confidence": detection_confidence,
            "frame_interval": frame_interval,
            "timezone": timezone,
            "exit_timeout": exit_timeout,
            "roosting_threshold": roosting_threshold,
            "telegram_enabled": telegram_enabled,
            "telegram_channel": telegram_channel,
        }

        # Handle optional detection_confidence_ir
        if detection_confidence_ir:
            updated_fields["detection_confidence_ir"] = float(detection_confidence_ir)
        elif "detection_confidence_ir" in existing_config:
            # Remove if cleared
            del existing_config["detection_confidence_ir"]

        existing_config.update(updated_fields)

        # Validate: roosting_threshold must be > exit_timeout
        if roosting_threshold <= exit_timeout:
            return HTMLResponse(
                '<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
                "Error: Roosting threshold must be greater than exit timeout"
                "</div>",
                status_code=400,
            )

        # Save merged config
        config_service.write_config(stream["config_path"], existing_config)

        # Restart if requested
        message = "Configuration saved successfully!"
        if action == "save_restart":
            print(f"[CONFIG] Restarting container: {stream['container_name']}")
            success = docker_service.restart_container(stream["container_name"])
            if success:
                message = "Configuration saved and container restarting..."
            else:
                message = "Configuration saved but container restart failed!"
                print(f"[CONFIG] Failed to restart {stream['container_name']}")

        # Check if this is an HTMX request
        if request.headers.get("HX-Request"):
            # HTMX request - return HTML fragment with auto-dismiss
            return HTMLResponse(
                f'<div class="bg-green-600/20 border-2 border-green-600 text-green-400 '
                f'px-4 py-3 rounded font-medium">'
                f"✓ {message}"
                f"</div>"
                f"<script>setTimeout(() => {{ "
                f'document.getElementById("save-feedback").innerHTML = ""; '
                f"}}, 5000);</script>"
            )
        else:
            # Regular form POST - redirect back to config page
            return RedirectResponse(url=f"/streams/{stream_id}/config", status_code=303)

    except Exception as e:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
                f"Error: {str(e)}"
                f"</div>",
                status_code=500,
            )
        else:
            raise HTTPException(status_code=500, detail=str(e))


@router.post("/streams")
async def create_new_stream(
    stream_id: str = Form(...),
    name: str = Form(...),
    video_source: str = Form(...),
    timezone: str = Form("+00:00"),
    telegram_enabled: bool = Form(False),
    telegram_channel: str = Form(""),
):
    """Create a new stream."""
    # Validate first
    valid, error = validate_stream_id(stream_id)
    if not valid:
        return HTMLResponse(
            f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded mb-4">'
            f"Error: {error}"
            f"</div>",
            status_code=400,
        )

    success, message = create_stream(
        stream_id=stream_id,
        name=name,
        video_source=video_source,
        timezone=timezone,
        telegram_enabled=telegram_enabled,
        telegram_channel=telegram_channel,
    )

    if success:
        return HTMLResponse(
            f'<div class="bg-green-600/20 border border-green-600 text-green-400 '
            f'px-4 py-3 rounded mb-4">'
            f'<p class="font-medium mb-2">✓ {message}</p>'
            f'<p class="text-sm mb-3">The detection container is now running. '
            f"Restart the admin to see the new stream in the overview.</p>"
            f'<button hx-post="/api/admin/restart" '
            f'        hx-swap="outerHTML" '
            f'        class="bg-amber-600 hover:bg-amber-500 px-4 py-2 rounded '
            f'font-medium text-white">'
            f"    ↻ Restart Admin Now"
            f"</button>"
            f"</div>"
        )
    else:
        return HTMLResponse(
            f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded mb-4">'
            f"Error: {message}"
            f"</div>",
            status_code=400,
        )


@router.post("/admin/restart")
async def restart_admin():
    """Restart the admin container."""
    success, message = restart_admin_container()

    if success:
        return HTMLResponse(
            '<div class="bg-blue-600/20 border border-blue-600 '
            'text-blue-400 px-4 py-3 rounded">'
            '<p class="font-medium">↻ Restarting admin container...</p>'
            '<p class="text-sm mt-1">This page will be unavailable for a few seconds. '
            '<a href="/" class="underline">Click here</a> to return to the overview '
            "once it's back.</p>"
            "</div>"
        )
    else:
        return HTMLResponse(
            '<div class="bg-red-600/20 border border-red-600 '
            'text-red-400 px-4 py-2 rounded">'
            f"Error restarting: {message}<br>"
            '<span class="text-sm">Manual restart: '
            "<code>docker restart kanyo-admin-web</code></span>"
            "</div>",
            status_code=500,
        )


@router.post("/streams/{stream_id}/cleanup-tmp")
async def cleanup_temp_files(stream_id: str):
    """Clean up incomplete recording files (.tmp) for a stream."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Run cleanup
    result = file_service.cleanup_temp_files(stream_id)

    # Format bytes for display
    mb_freed = result["bytes_freed"] / (1024 * 1024)

    return {
        "success": True,
        "files_deleted": result["files_deleted"],
        "bytes_freed": result["bytes_freed"],
        "mb_freed": round(mb_freed, 2),
        "message": f"Deleted {result['files_deleted']} temp files, freed {mb_freed:.2f} MB",
    }


@router.post("/streams/{stream_id}/cleanup-logs")
async def cleanup_log_files(stream_id: str):
    """Clean up FFmpeg log files (.ffmpeg.log) for a stream."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Run cleanup
    result = file_service.cleanup_log_files(stream_id)

    # Format bytes for display
    mb_freed = result["bytes_freed"] / (1024 * 1024)

    return {
        "success": True,
        "files_deleted": result["files_deleted"],
        "bytes_freed": result["bytes_freed"],
        "mb_freed": round(mb_freed, 2),
        "message": f"Deleted {result['files_deleted']} log files, freed {mb_freed:.2f} MB",
    }


@router.get("/system/status")
async def get_system_status():
    """Get system monitoring stats (CPU, memory, disk, GPU, Docker) as HTML."""
    stats = system_service.get_system_stats()
    return templates.TemplateResponse(
        "components/system_status.html", {"request": {}, "stats": stats}, media_type="text/html"
    )
