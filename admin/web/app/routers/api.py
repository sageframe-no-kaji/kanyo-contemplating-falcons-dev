"""API router for JSON endpoints."""

import html
import json
from pathlib import Path
from urllib.parse import quote

from app.services import (
    clip_service,
    compose_snippet,
    config_service,
    docker_service,
    file_service,
    log_service,
    stream_service,
    system_service,
)
from app.services.stream_manager import (
    build_stream_config,
    create_stream,
    restart_admin_container,
    validate_stream_form,
)
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel


def _h(value) -> str:
    """Escape value for HTML text/attribute context (covers both: quote=True)."""
    return html.escape(str(value), quote=True)


def _u(value) -> str:
    """Percent-encode value for use as a URL path component (preserves '/')."""
    return quote(str(value), safe="/")


def _js(value) -> str:
    """Render value as a JS literal safe for embedding inside an HTML attribute.

    JSON-encoding handles JS-side escaping (quotes, backslashes, control chars);
    html.escape then makes it safe inside an attribute value. Browser un-escapes
    the attribute, JS parser sees a valid string literal.
    """
    return html.escape(json.dumps(str(value)), quote=True)


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


def _refresh_stream_card(stream: dict) -> None:
    """Populate stream dict with current status + clip info for the card fragment."""
    status = docker_service.get_container_status(stream["container_name"])
    stream["status"] = status["status"]
    stream["uptime"] = status.get("uptime", "")
    stream["latest_thumbnail"] = clip_service.get_latest_thumbnail(
        stream["clips_path"], stream["id"]
    )
    stream["today_visits"] = clip_service.get_today_visits(
        stream["clips_path"], stream.get("timezone", "UTC")
    )
    stream["last_event"] = clip_service.get_last_event(
        stream["clips_path"], stream.get("timezone", "UTC")
    )


@router.post("/streams/{stream_id}/restart")
async def restart_stream(stream_id: str):
    """Restart container and return HTML fragment."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    success = docker_service.restart_container(stream["container_name"])

    if not success:
        raise HTTPException(status_code=500, detail="Failed to restart container")

    _refresh_stream_card(stream)

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

    _refresh_stream_card(stream)

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

    _refresh_stream_card(stream)

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
        return f'<p class="text-zinc-400">No clips in the last {_h(hours)} hours</p>'

    # Render the clips grid (same as detail page)
    html_out = """
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

    stream_id_url = _u(stream_id)
    for clip in clips[:12]:
        thumb_name = clip["filename"].rsplit(".", 1)[0] + ".jpg"
        date_str = clip["date"]
        clip_type_color = {
            "arrival": "bg-green-600",
            "departure": "bg-red-600",
            "visit": "bg-blue-600",
        }.get(clip["type"], "bg-zinc-600")

        clip_url = f"/clips/{stream_id_url}/{_u(date_str)}/{_u(clip['filename'])}"
        thumb_url = f"/clips/{stream_id_url}/{_u(date_str)}/{_u(thumb_name)}"
        label = f"{clip['type']} at {clip['time']}"

        html_out += f"""
        <div class="aspect-video bg-zinc-900 rounded overflow-hidden relative group cursor-pointer"
             onclick="playClip({_js(clip_url)}, {_js(label)})">
            <img src="{_h(thumb_url)}"
                 class="w-full h-full object-cover"
                 onerror="this.style.display='none'"
                 alt="{_h(label)}">
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
                            {_h(clip['type'])}
                        </span>
                    </div>
                    <div class="text-[11px] text-white/90">
                        {_h(date_str)} {_h(clip['time'])}
                    </div>
                </div>
            </div>
            <div class="absolute inset-0 flex items-center justify-center opacity-0 \
group-hover:opacity-100 transition bg-black/30">
                <span class="text-4xl">▶</span>
            </div>
        </div>
        """

    html_out += "</div>"
    return html_out


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
        return f'<p class="text-zinc-500 text-sm">No events in the last {_h(label)}</p>'

    stream_id_url = _u(stream_id)
    html_out = '<div class="space-y-2 max-h-96 overflow-y-auto">'
    for event in events:
        clip_url = f"/clips/{stream_id_url}/{_u(event['date'])}/{_u(event['filename'])}"
        label = f"{event['type']} at {event['time']}"
        dt_attr = _h(event["datetime"].isoformat())
        html_out += f"""
    <div class="text-sm hover:bg-zinc-700 rounded px-2 py-1 -mx-2 cursor-pointer transition"
         onclick="playClip({_js(clip_url)}, {_js(label)})">
        <span class="text-zinc-400">
            {_h(event['date'])} {_h(event['time'])}
            <span class="event-local-time text-zinc-500" data-datetime="{dt_attr}"></span>
        </span>
        <span class="text-white ml-2">{_h(event['type'])}</span>
    </div>"""
    html_out += "</div>"
    return html_out


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

    # Get logs from file (timestamps are UTC-aware); logs dir comes from
    # stream discovery (issue #5) so both mount layouts work.
    logs = log_service.get_logs(
        stream_id,
        since=since,
        lines=lines,
        levels=level_list,
        show_context=show_context,
        log_dir=Path(stream["data_path"]) / "logs",
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
            f"{_h(timestamp_str)} (stream local) | "
            f'<span class="log-level">{_h(level_padded)}</span> | '
            f'{_h(log_entry["module"])} | {_h(log_entry["message"])}'
        )

        html_lines.append(f'<div class="log-line" data-level="{_h(level)}">{formatted_line}</div>')

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
    # Display metadata fields
    display_short_name: str = Form(""),
    display_location: str = Form(""),
    display_latitude: str = Form(""),
    display_longitude: str = Form(""),
    display_species: str = Form(""),
    display_nest_status: str = Form(""),
    display_maintainer: str = Form(""),
    display_maintainer_url: str = Form(""),
    display_description: str = Form(""),
    display_order: str = Form(""),
):
    """Update stream configuration."""
    try:
        # Get stream info
        stream = stream_service.get_stream(stream_id)
        if not stream:
            return HTMLResponse(
                f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
                f"Error: Stream {_h(stream_id)} not found"
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

        # Update display metadata
        display_data = {}
        if display_short_name:
            display_data["short_name"] = display_short_name
        if display_location:
            display_data["location"] = display_location
        if display_latitude and display_longitude:
            try:
                lat = float(display_latitude)
                lon = float(display_longitude)
                display_data["coordinates"] = [lat, lon]
            except ValueError:
                pass  # Skip invalid coordinates
        if display_species:
            display_data["species"] = display_species
        if display_nest_status:
            display_data["nest_status"] = display_nest_status
        if display_maintainer:
            display_data["maintainer"] = display_maintainer
        if display_maintainer_url:
            display_data["maintainer_url"] = display_maintainer_url
        if display_description:
            display_data["description"] = display_description
        if display_order:
            try:
                display_data["order"] = int(display_order)
            except ValueError:
                pass

        # Merge into existing display dict to preserve fields not in this form (e.g. thumbnail_url)
        existing_display = existing_config.get("display", {})
        existing_display.update(display_data)
        updated_fields["display"] = existing_display

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
                f"✓ {_h(message)}"
                f"</div>"
                f"<script>setTimeout(() => {{ "
                f'document.getElementById("save-feedback").innerHTML = ""; '
                f"}}, 5000);</script>"
            )
        else:
            # Regular form POST - redirect back to config page
            return RedirectResponse(url=f"/streams/{_u(stream_id)}/config", status_code=303)

    except Exception as e:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
                f"Error: {_h(str(e))}"
                f"</div>",
                status_code=500,
            )
        else:
            raise HTTPException(status_code=500, detail=str(e))


class ReorderRequest(BaseModel):
    stream_ids: list[str]


@router.post("/streams/reorder")
async def reorder_streams(body: ReorderRequest):
    """Write display.order to each stream's config.yaml based on submitted order."""
    errors = []
    for index, stream_id in enumerate(body.stream_ids):
        stream = stream_service.get_stream(stream_id)
        if not stream:
            errors.append(stream_id)
            continue
        existing_config = config_service.read_config(stream["config_path"])
        display = existing_config.get("display", {})
        display["order"] = index + 1
        existing_config["display"] = display
        config_service.write_config(stream["config_path"], existing_config)

    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=207)
    return JSONResponse({"ok": True})


@router.post("/streams")
async def create_new_stream(
    stream_id: str = Form(...),
    name: str = Form(...),
    video_source: str = Form(...),
    short_name: str = Form(...),
    location: str = Form(...),
    timezone: str = Form(...),
    species: str = Form(...),
    latitude: str = Form(""),
    longitude: str = Form(""),
    maintainer: str = Form(""),
    maintainer_url: str = Form(""),
    description: str = Form(""),
    telegram_enabled: bool = Form(False),
    telegram_channel: str = Form(""),
    detection_confidence: float = Form(0.3),
    detection_confidence_ir: str = Form(""),
    frame_interval: int = Form(3),
    exit_timeout: int = Form(90),
    roosting_threshold: int = Form(1800),
    detect_any_animal: bool = Form(False),
    presence_enabled: bool = Form(False),
    significance_filter_enabled: bool = Form(False),
    bird_count_enabled: bool = Form(False),
):
    """Create the on-disk stream definition (issue #6).

    Bounded scope: writes /data/kanyo-<id>/ (config.yaml + clips/ + logs/)
    and nothing else. No container is created — the success UI surfaces the
    manual next steps (compose service block via the template's profile
    pattern, Telegram channel setup, ZFS note).
    """

    def _error(messages: list[str], status: int = 400) -> HTMLResponse:
        items = "".join(f"<li>{_h(m)}</li>" for m in messages)
        return HTMLResponse(
            f'<div class="bg-red-600/20 border border-red-600 text-red-400 '
            f'px-4 py-2 rounded mb-4"><p class="font-medium">Error:</p>'
            f'<ul class="list-disc list-inside text-sm mt-1">{items}</ul></div>',
            status_code=status,
        )

    # Parse optional numerics
    try:
        conf_ir = float(detection_confidence_ir) if detection_confidence_ir else None
    except ValueError:
        return _error(["detection_confidence_ir must be a number"])

    lat = lon = None
    if latitude or longitude:
        try:
            lat = float(latitude)
            lon = float(longitude)
        except (TypeError, ValueError):
            return _error(["Coordinates need both latitude and longitude as numbers"])

    if telegram_enabled and not telegram_channel.strip():
        return _error(["Telegram is enabled but no channel is set"])

    # Validate everything before touching disk
    errors = validate_stream_form(
        stream_id=stream_id,
        video_source=video_source,
        timezone=timezone,
        detection_confidence=detection_confidence,
        detection_confidence_ir=conf_ir,
        frame_interval=frame_interval,
        exit_timeout=exit_timeout,
        roosting_threshold=roosting_threshold,
    )
    if errors:
        return _error(errors)

    config = build_stream_config(
        name=name,
        video_source=video_source,
        timezone=timezone,
        short_name=short_name,
        location=location,
        species=species,
        latitude=lat,
        longitude=lon,
        maintainer=maintainer,
        maintainer_url=maintainer_url,
        description=description,
        telegram_enabled=telegram_enabled,
        telegram_channel=telegram_channel,
        detection_confidence=detection_confidence,
        detection_confidence_ir=conf_ir,
        frame_interval=frame_interval,
        exit_timeout=exit_timeout,
        roosting_threshold=roosting_threshold,
        detect_any_animal=detect_any_animal,
        presence_enabled=presence_enabled,
        significance_filter_enabled=significance_filter_enabled,
        bird_count_enabled=bird_count_enabled,
    )

    success, message = create_stream(stream_id, config)
    if not success:
        return _error([message])

    stream_id_url = _u(stream_id)
    return HTMLResponse(
        f'<div class="bg-green-600/20 border border-green-600 text-green-400 '
        f'px-4 py-3 rounded mb-4">'
        f"<p class=\"font-medium mb-2\">✓ Stream '{_h(name)}' created at {_h(message)}</p>"
        f'<p class="text-sm mb-1">config.yaml, clips/ and logs/ are in place. '
        f'The stream is already visible in the <a href="/" class="underline">overview</a> '
        f"(container status will show as not found until a detector runs).</p>"
        f'<div class="text-sm text-zinc-300 mt-3">'
        f'<p class="font-medium text-green-300 mb-1">Manual next steps:</p>'
        f'<ol class="list-decimal list-inside space-y-1">'
        f'<li>Review the config in the <a href="/streams/{stream_id_url}/config" '
        f'class="underline">config editor</a>.</li>'
        f"<li>Start a detector: paste the generated snippet below into the host "
        f"compose + .env, then run the start command. It follows the template's "
        f"profile pattern (<code class='bg-zinc-700 px-1 rounded'>docker/"
        f"docker-compose.yml</code>, bigbear example). Container creation is "
        f"deliberately not automated (see issue #7).</li>"
        f"<li>ZFS hosts: if this stream should live on its own dataset, create it "
        f"<em>before</em> pointing the detector at it (e.g. "
        f"<code class='bg-zinc-700 px-1 rounded'>sudo zfs create "
        f"rpool/sage/kanyo/{_h(stream_id)}</code>) — dataset creation needs root and "
        f"cannot be done from this container.</li>"
        f"<li>Telegram: create the channel in the Telegram app, add the bot as a "
        f"channel admin, then set the channel in the config editor. Both steps "
        f"require a human in the Telegram UI.</li>"
        f"</ol>"
        f'<pre class="bg-zinc-900 text-zinc-200 text-xs rounded p-3 mt-3 '
        f'overflow-x-auto whitespace-pre">{_h(compose_snippet.build_snippet(stream_id))}</pre>'
        f'<p class="text-xs text-zinc-500 mt-1">Snippet stays available at '
        f'<a href="/api/streams/{stream_id_url}/compose-snippet" class="underline">'
        f"/api/streams/{_h(stream_id)}/compose-snippet</a>.</p>"
        f"</div></div>"
    )


@router.get("/streams/{stream_id}/compose-snippet")
async def get_compose_snippet(stream_id: str):
    """Compose service block + .env additions for an existing stream dir.

    Bounded issue #7 slice: text to paste into the host compose — no live
    orchestration. Works for any discovered stream, so a detector can be
    added later for streams created before this endpoint existed.
    """
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    return PlainTextResponse(compose_snippet.build_snippet(stream_id))


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
            f"Error restarting: {_h(message)}<br>"
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

    # Run cleanup (clips dir from stream discovery, issue #5)
    result = file_service.cleanup_temp_files(stream_id, clips_path=stream["clips_path"])

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

    # Run cleanup (clips dir from stream discovery, issue #5)
    result = file_service.cleanup_log_files(stream_id, clips_path=stream["clips_path"])

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
