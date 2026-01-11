"""Page router for HTML templates."""

from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import socket

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
        stream["latest_thumbnail"] = clip_service.get_latest_thumbnail(
            stream["clips_path"], stream["id"]
        )
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
        },
    )


@router.get("/streams/new", response_class=HTMLResponse)
async def new_stream_page(request: Request):
    """New stream form."""
    return templates.TemplateResponse(
        "stream/new.html",
        {
            "request": request,
        },
    )


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

    # Get today's clips using STREAM timezone, not server timezone
    stream_tz = stream.get("timezone", "UTC")
    today = clip_service.get_stream_today(stream_tz)
    clips = clip_service.list_clips(stream["clips_path"], today)

    # Get deduplicated events from last 24 hours
    events = clip_service.get_recent_events(stream["clips_path"], stream_tz)

    return templates.TemplateResponse(
        "stream/detail.html",
        {
            "request": request,
            "stream": stream,
            "clips": clips,
            "events": events,
            "date": today,
        },
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
        },
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
        },
    )


@router.get("/streams/{stream_id}/files", response_class=HTMLResponse)
@router.get("/streams/{stream_id}/files/{path:path}", response_class=HTMLResponse)
async def stream_files(request: Request, stream_id: str, path: str = ""):
    """File browser for stream data directory."""
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
    is_file = current_path.is_file()
    file_content = None

    if current_path.is_dir():
        for item in sorted(current_path.iterdir()):
            items.append(
                {
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None,
                    "modified": datetime.fromtimestamp(item.stat().st_mtime),
                    "path": str(item.relative_to(base_path)),
                }
            )
    else:
        # If it's a file, try to read text content
        if current_path.suffix in [".log", ".txt", ".json", ".yaml", ".yml"]:
            try:
                file_content = current_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                file_content = None

    # Breadcrumb parts
    parts = [p for p in path.split("/") if p]
    breadcrumbs = [{"name": "Root", "path": ""}]
    for i, part in enumerate(parts):
        breadcrumbs.append({"name": part, "path": "/".join(parts[: i + 1])})

    return templates.TemplateResponse(
        "stream/files.html",
        {
            "request": request,
            "stream": stream,
            "items": items,
            "current_path": path,
            "breadcrumbs": breadcrumbs,
            "is_file": is_file,
            "file_content": file_content,
        },
    )
