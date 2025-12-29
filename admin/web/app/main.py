"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import settings


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
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
