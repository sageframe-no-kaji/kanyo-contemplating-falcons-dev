"""
Live stream tee manager for YouTube ingestion.

Runs a single ffmpeg process that reads a YouTube live stream and outputs
to BOTH a local proxy (for detection) and a segment recorder (for clips).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from kanyo.utils.encoder import detect_hardware_encoder
from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


class FFmpegTeeManager:
    """
    Manages single ffmpeg process with tee split for YouTube live streams.

    Outputs:
    1. Low-latency proxy (copy codec) for realtime detection
    2. Hardware-encoded segments for fast clip extraction

    Architecture:
        YouTube URL → ffmpeg → [proxy (copy) + segments (h264_hw)]
    """

    def __init__(
        self,
        stream_url: str,
        proxy_url: str,
        buffer_dir: str | Path,
        chunk_minutes: int = 10,
        encoder: str | None = None,
        fps: int = 30,
    ):
        """
        Initialize tee manager.

        Args:
            stream_url: Direct HLS/DASH URL from yt-dlp
            proxy_url: Local proxy URL (e.g., udp://127.0.0.1:12345)
            buffer_dir: Directory for segment files
            chunk_minutes: Segment duration in minutes
            encoder: Hardware encoder override (auto-detect if None)
            fps: Output framerate for segments (default: 30)
        """
        self.stream_url = stream_url
        self.proxy_url = proxy_url
        self.buffer_dir = Path(buffer_dir)
        self.chunk_minutes = chunk_minutes
        self.encoder = encoder or detect_hardware_encoder()
        self.fps = fps
        self.process: subprocess.Popen | None = None

        # Create buffer directory
        self.buffer_dir.mkdir(parents=True, exist_ok=True)

    def build_command(self) -> list[str]:
        """
        Build ffmpeg tee command with platform-specific encoder.

        Returns list of command arguments.
        """
        chunk_seconds = self.chunk_minutes * 60
        segment_pattern = str(self.buffer_dir / "segment_%Y%m%d_%H%M%S.mp4")

        # Base command - input with low-latency flags
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-re",  # Read at native framerate
            "-i",
            self.stream_url,
        ]

        # Output 1: Proxy (copy codec for low latency)
        cmd.extend(
            [
                "-map",
                "0:v",
                "-c:v",
                "copy",
                "-f",
                "mpegts",
                self.proxy_url,
            ]
        )

        # Output 2: Segments (hardware encoded)
        cmd.extend(["-map", "0:v"])

        # Platform-specific encoder flags
        if self.encoder == "h264_vaapi":
            # Intel/AMD VAAPI on Linux
            cmd.extend(
                [
                    "-vaapi_device",
                    "/dev/dri/renderD128",
                    "-vf",
                    "format=nv12,hwupload",
                    "-c:v",
                    "h264_vaapi",
                    "-rc_mode",
                    "vbr",
                    "-b:v",
                    "4M",
                ]
            )
        elif self.encoder == "h264_videotoolbox":
            # macOS VideoToolbox
            cmd.extend(["-c:v", "h264_videotoolbox", "-crf", "23"])
        elif self.encoder == "h264_nvenc":
            # NVIDIA NVENC
            cmd.extend(["-c:v", "h264_nvenc", "-preset", "fast", "-b:v", "4M"])
        else:
            # Software fallback
            cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "23"])

        # Common segment output settings
        cmd.extend(
            [
                "-r",
                str(self.fps),
                "-f",
                "segment",
                "-segment_time",
                str(chunk_seconds),
                "-strftime",
                "1",
                segment_pattern,
            ]
        )

        return cmd

    def start(self) -> bool:
        """
        Start the ffmpeg tee process.

        Returns True if started successfully.
        """
        if self.process is not None:
            logger.warning("Tee process already running")
            return True

        cmd = self.build_command()
        logger.info(f"Starting ffmpeg tee with encoder: {self.encoder}")
        logger.debug(f"Command: {' '.join(cmd)}")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Give it a moment to start
            time.sleep(2)

            # Check if it crashed immediately
            if self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                logger.error(f"Tee process died immediately: {stderr}")
                self.process = None
                return False

            logger.info("✅ Tee process started")
            return True

        except Exception as e:
            logger.error(f"Failed to start tee process: {e}")
            return False

    def stop(self) -> None:
        """Stop the ffmpeg tee process."""
        if self.process is None:
            return

        logger.info("Stopping tee process...")
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Tee process didn't terminate, killing")
            self.process.kill()
            self.process.wait()
        except Exception as e:
            logger.error(f"Error stopping tee process: {e}")
        finally:
            self.process = None
            logger.info("Tee process stopped")

    def is_running(self) -> bool:
        """Check if tee process is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def get_recent_segments(self, limit: int = 10) -> list[Path]:
        """
        Get most recent segment files for clip extraction.

        Args:
            limit: Maximum number of segments to return

        Returns sorted list of segment paths (newest first).
        """
        segments = sorted(
            self.buffer_dir.glob("segment_*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return segments[:limit]

    def cleanup_old_segments(self, keep_minutes: int = 60) -> int:
        """
        Remove segment files older than keep_minutes.

        Args:
            keep_minutes: Keep segments newer than this many minutes

        Returns number of files deleted.
        """
        cutoff_time = time.time() - (keep_minutes * 60)
        deleted = 0

        for segment in self.buffer_dir.glob("segment_*.mp4"):
            if segment.stat().st_mtime < cutoff_time:
                try:
                    segment.unlink()
                    deleted += 1
                    logger.debug(f"Deleted old segment: {segment.name}")
                except Exception as e:
                    logger.warning(f"Failed to delete {segment.name}: {e}")

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old segment(s)")

        return deleted

    def __enter__(self) -> "FFmpegTeeManager":
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.stop()
