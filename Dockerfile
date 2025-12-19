# Base image: Debian with Python 3.11
FROM python:3.11-slim-bookworm

# Build args for runtime user/group (defaults to 1000:1000)
ARG APP_UID=1000
ARG APP_GID=1000

# Install system dependencies
# ffmpeg: video processing
# libgl1, libglib2.0-0: OpenCV dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create non-root user and group (before installing Python deps)
RUN groupadd -g ${APP_GID} app && \
    useradd -u ${APP_UID} -g ${APP_GID} -m -s /bin/bash app

# Create directories for runtime and YOLO cache
RUN mkdir -p /app/clips /app/logs /app/.config

# Copy requirements and install Python dependencies
# Do this BEFORE copying code (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Set YOLO cache and config directories (before model download)
ENV YOLO_CONFIG_DIR=/app/.config
ENV YOLO_CACHE_DIR=/app/.config

# Download YOLO model directly into app-owned cache directory
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

# Copy application code INTO the image (not bind mounted)
COPY src/ ./src/

# Set ownership of runtime-writable directories only
RUN chown -R app:app /app/src /app/clips /app/logs /app/.config

# Set Python path so imports work
ENV PYTHONPATH=/app/src

# Suppress OpenCV h264 warnings
ENV OPENCV_FFMPEG_LOGLEVEL=-8

# Set safe default umask for runtime file creation
ENV UMASK=027

# Switch to non-root user (final privilege change)
USER app

# Run the detection monitor with umask applied
CMD ["/bin/sh", "-c", "umask ${UMASK} && exec python -m kanyo.detection.realtime_monitor"]
