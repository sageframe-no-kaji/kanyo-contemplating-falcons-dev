"""API router for JSON endpoints."""

from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.services import stream_service, docker_service, config_service


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
        "components/stream_card.html",
        {"request": {}, "stream": stream},
        media_type="text/html"
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
        "components/stream_card.html",
        {"request": {}, "stream": stream},
        media_type="text/html"
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
        "components/stream_card.html",
        {"request": {}, "stream": stream},
        media_type="text/html"
    )


@router.get("/streams/{stream_id}/logs")
async def get_logs(stream_id: str, lines: int = 100, level: str = None):
    """Get container logs."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    logs = docker_service.get_logs(stream["container_name"], lines)

    # Filter by level if specified
    if level:
        log_lines = logs.split('\n')
        filtered_lines = [line for line in log_lines if level.upper() in line]
        logs = '\n'.join(filtered_lines)

    return {"logs": logs}


@router.put("/streams/{stream_id}/config")
async def update_config(
    stream_id: str,
    action: str = Form("save"),
    stream_name: str = Form(...),
    video_source: str = Form(...),
    detection_confidence: float = Form(...),
    frame_interval: int = Form(...),
    timezone: str = Form(...),
    exit_timeout: int = Form(...),
    roosting_threshold: int = Form(...),
    telegram_enabled: bool = Form(False),
    telegram_channel: str = Form(""),
):
    """Update stream configuration."""
    try:
        # Build config dict
        config = {
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

        # Validate: roosting_threshold must be > exit_timeout
        if roosting_threshold <= exit_timeout:
            return HTMLResponse(
                '<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
                'Error: Roosting threshold must be greater than exit timeout'
                '</div>',
                status_code=400
            )

        # Get stream info
        stream = stream_service.get_stream(stream_id)
        if not stream:
            return HTMLResponse(
                f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
                f'Error: Stream {stream_id} not found'
                f'</div>',
                status_code=404
            )

        # Save config
        config_service.write_config(stream["config_path"], config)

        # Restart if requested
        message = "Configuration saved successfully!"
        if action == "save_restart":
            docker_service.restart_container(stream["container_name"])
            message = "Configuration saved and container restarting..."

        return HTMLResponse(
            f'<div class="bg-green-600/20 border border-green-600 text-green-400 px-4 py-2 rounded">'
            f'{message}'
            f'</div>'
        )

    except Exception as e:
        return HTMLResponse(
            f'<div class="bg-red-600/20 border border-red-600 text-red-400 px-4 py-2 rounded">'
            f'Error: {str(e)}'
            f'</div>',
            status_code=500
        )
