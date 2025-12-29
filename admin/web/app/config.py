"""Application configuration."""

import os
from pathlib import Path


class Settings:
    """Application settings."""

    # Path to stream data
    DATA_PATH: Path = Path(os.getenv("KANYO_DATA_PATH", "/data"))

    # Environment: development or production
    ENV: str = os.getenv("KANYO_ENV", "development")

    # App metadata
    APP_NAME: str = "Kanyo Admin"
    VERSION: str = "1.0.0"


settings = Settings()
