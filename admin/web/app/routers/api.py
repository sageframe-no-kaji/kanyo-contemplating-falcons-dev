"""API router for JSON endpoints."""

from fastapi import APIRouter, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.services import stream_service, docker_service, config_service, clip_service, log_service
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
async def get_clips_for_date(request: Request, stream_id: str, offset: int = 0):
    """Get clips for a specific date offset (0=today, 1=yesterday, etc.) in stream's timezone."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Calculate the date using STREAM timezone, not server timezone
    stream_tz = stream.get("timezone", "UTC")
    date_str = clip_service.get_stream_date_offset(stream_tz, offset)

    # Get clips for that date
    clips = clip_service.list_clips(stream["clips_path"], date_str)

    # Filter to only show videos (skip still images)
    clips = [c for c in clips if c["is_video"]]

    # Render clips grid HTML
    if not clips:
        return '<p class="text-zinc-400">No clips for this date</p>'

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
        clip_type_color = {
            "arrival": "bg-green-600",
            "departure": "bg-red-600",
            "visit": "bg-blue-600",
        }.get(clip["type"], "bg-zinc-600")

        html += f"""
        <div class="aspect-video bg-zinc-900 rounded overflow-hidden relative group cursor-pointer"
             onclick="playClip('/clips/{stream_id}/{date_str}/{clip['filename']}', '{clip['type']} at {clip['time']}')">
            <img src="/clips/{stream_id}/{date_str}/{thumb_name}"
                 class="w-full h-full object-cover"
                 onerror="this.style.display='none'"
                 alt="{clip['type']} at {clip['time']}">
            <div class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-2">
                <div class="flex flex-col gap-1">
                    <div class="flex items-center justify-between text-xs">
                        <span class="flex items-center gap-1">
                            <span class="text-[10px] font-bold bg-white/20 px-1 rounded">VID</span>
                        </span>
                        <span class="px-1.5 py-0.5 rounded text-[10px] font-medium {clip_type_color}">
                            {clip['type']}
                        </span>
                    </div>
                    <div class="text-[11px] text-white/90">
                        {date_str} {clip['time']}
                    </div>
                </div>
            </div>
            <div class="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition bg-black/30"><span class="text-4xl">▶</span></div>
        </div>
        """

    html += "</div>"
    return html


@router.get("/streams/{stream_id}/logs")
async def get_logs(
    stream_id: str,
    since: str = "startup",
    lines: int = 500,
    levels: str = "INFO,EVENT,WARNING,ERROR",
):
    """
    Get logs from kanyo.log file with filtering.

    Args:
        stream_id: Stream identifier
        since: Time range - "startup", "1h", "24h", "7d", "all"
        lines: Maximum number of lines to return
        levels: Comma-separated log levels to include

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
    level_list = [l.strip() for l in levels.split(",") if l.strip()] if levels else None

    # Get logs from file (timestamps are UTC-aware)
    logs = log_service.get_logs(stream_id, since=since, lines=lines, levels=level_list)

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
            f'{timestamp_str} (stream local) | '
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
            docker_service.restart_container(stream["container_name"])
            message = "Configuration saved and container restarting..."

        # Check if this is an HTMX request
        if request.headers.get("HX-Request"):
            # HTMX request - return HTML fragment
            return HTMLResponse(
                f'<div class="bg-green-600/20 border border-green-600 text-green-400 px-4 py-2 rounded">'
                f"{message}"
                f"</div>"
            )
        else:
            # Regular form POST - redirect back to config page
            return RedirectResponse(
                url=f"/streams/{stream_id}/config",
                status_code=303
            )

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
            f'<div class="bg-green-600/20 border border-green-600 text-green-400 px-4 py-3 rounded mb-4">'
            f'<p class="font-medium mb-2">✓ {message}</p>'
            f'<p class="text-sm mb-3">The detection container is now running. Restart the admin to see the new stream in the overview.</p>'
            f'<button hx-post="/api/admin/restart" '
            f'        hx-swap="outerHTML" '
            f'        class="bg-amber-600 hover:bg-amber-500 px-4 py-2 rounded font-medium text-white">'
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
            '<div class="bg-blue-600/20 border border-blue-600 text-blue-400 px-4 py-3 rounded">'
            '<p class="font-medium">↻ Restarting admin container...</p>'
            '<p class="text-sm mt-1">This page will be unavailable for a few seconds. '
            '<a href="/" class="underline">Click here</a> to return to the overview once it\'s back.</p>'
            "</div>"
        )
    else:
        return HTMLResponse(
            f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
            f"Error restarting: {message}<br>"
            f'<span class="text-sm">Manual restart: <code>docker restart kanyo-admin-web</code></span>'
            f"</div>",
            status_code=500,
        )
