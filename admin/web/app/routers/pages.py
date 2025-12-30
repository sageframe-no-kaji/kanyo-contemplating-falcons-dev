"""Page router for HTML templates."""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import socket
from datetime import datetime

from app.services import stream_service, docker_service, clip_service, config_service


router = APIRouter()

# Set up templates
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    """Render overview page with all streams."""
    streams = stream_service.discover_streams()

    # Add status and clip info to each stream
    for stream in streams:
        # Get container status
        status = docker_service.get_container_status(stream["container_name"])
        stream["status"] = status["status"]
        stream["uptime"] = status.get("uptime", "")

        # Get clip info
        stream["latest_thumbnail"] = clip_service.get_latest_thumbnail(stream["clips_path"], stream["id"])
        stream["today_visits"] = clip_service.get_today_visits(stream["clips_path"])
        stream["last_event"] = clip_service.get_last_event(stream["clips_path"])

    # Get hostname
    hostname = socket.gethostname()

    return templates.TemplateResponse(
        "overview.html",
        {
            "request": request,
            "streams": streams,
            "hostname": hostname,
        }
    )


@router.get("/streams/new", response_class=HTMLResponse)
async def new_stream_page(request: Request):
    """New stream form."""
    return templates.TemplateResponse("stream/new.html", {
        "request": request,
    })


@router.get("/streams/{stream_id}", response_class=HTMLResponse)
async def stream_detail(request: Request, stream_id: str):
    """Render stream detail page."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Get container status
    status = docker_service.get_container_status(stream["container_name"])
    stream["status"] = status["status"]
    stream["uptime"] = status.get("uptime", "")

    # Get today's clips
    today = datetime.now().strftime("%Y-%m-%d")
    clips = clip_service.list_clips(stream["clips_path"], today)

    # Get deduplicated events for today
    events = clip_service.get_today_events(stream["clips_path"])

    return templates.TemplateResponse(
        "stream/detail.html",
        {
            "request": request,
            "stream": stream,
            "clips": clips,
            "events": events,
            "date": today,
        }
    )


@router.get("/streams/{stream_id}/config", response_class=HTMLResponse)
async def stream_config(request: Request, stream_id: str):
    """Render config editor page."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Load current config
    config = config_service.read_config(stream["config_path"])

    return templates.TemplateResponse(
        "stream/config.html",
        {
            "request": request,
            "stream": stream,
            "config": config,
        }
    )


@router.get("/streams/{stream_id}/logs", response_class=HTMLResponse)
async def stream_logs(request: Request, stream_id: str):
    """Render logs viewer page."""
    stream = stream_service.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Get logs
    logs = docker_service.get_logs(stream["container_name"], lines=100)

    return templates.TemplateResponse(
        "stream/logs.html",
        {
            "request": request,
            "stream": stream,
            "logs": logs,
        }
    )
