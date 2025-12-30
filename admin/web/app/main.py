"""FastAPI application entry point."""

import secrets
import os
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path

from app.config import settings


# Basic auth security
security = HTTPBasic()


def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify basic authentication credentials."""
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD")

    if not admin_pass:
        # No password set - allow access (dev mode)
        return

    is_user_ok = secrets.compare_digest(credentials.username, admin_user)
    is_pass_ok = secrets.compare_digest(credentials.password, admin_pass)

    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    dependencies=[Depends(verify_auth)],  # Apply auth to all routes
)

# Get app directory
app_dir = Path(__file__).parent

# Mount static files
app.mount("/static", StaticFiles(directory=app_dir / "static"), name="static")

# Import routers after app creation to avoid circular imports
from app.routers import api, pages

# Include routers
app.include_router(api.router, prefix="/api")
app.include_router(pages.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/clips/{stream_id}/{date}/{filename}")
async def serve_clip(stream_id: str, date: str, filename: str):
    """Serve clip files (videos and thumbnails) from /data/{stream}/clips/."""
    file_path = settings.DATA_PATH / stream_id / "clips" / date / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)


@app.get("/data/{stream_id}/{path:path}")
async def serve_data_file(stream_id: str, path: str):
    """Serve any file from stream's data directory."""
    file_path = settings.DATA_PATH / stream_id / path

    # Security: ensure path stays within stream directory
    try:
        file_path.resolve().relative_to((settings.DATA_PATH / stream_id).resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    return FileResponse(file_path)
