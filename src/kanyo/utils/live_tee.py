"""
Live stream tee manager for YouTube ingestion.

Runs a single ffmpeg process that reads a YouTube live stream and outputs
to BOTH a local proxy (for detection) and a segment recorder (for clips).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
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

        # Base command - YouTube-optimized input flags
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "fatal",  # Only show fatal errors (suppresses h264 warnings)
            "-err_detect",
            "ignore_err",  # Ignore decode errors, keep streaming
            "-fflags",
            "+genpts+discardcorrupt",  # Generate PTS, discard corrupt packets
            "-analyzeduration",
            "10000000",  # Analyze 10 seconds
            "-probesize",
            "10000000",  # Probe 10MB
            "-reconnect",
            "1",  # Auto-reconnect on failure
            "-reconnect_streamed",
            "1",  # Reconnect for live streams
            "-reconnect_delay_max",
            "5",  # Max 5 second reconnect delay
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
                "-reset_timestamps",
                "1",
                "-movflags",
                "+faststart+frag_keyframe",
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

    @staticmethod
    def get_segment_timerange(
        segment_path: Path, chunk_minutes: int = 10
    ) -> tuple[datetime, datetime]:
        """
        Parse segment filename to get time range.

        Args:
            segment_path: Path to segment file (e.g., segment_20231217_143000.mp4)
            chunk_minutes: Duration of each segment in minutes

        Returns:
            Tuple of (start_time, end_time) for the segment

        Raises:
            ValueError: If filename doesn't match expected pattern
        """
        # Pattern: segment_YYYYMMDD_HHMMSS.mp4
        pattern = r"segment_(\d{8})_(\d{6})\.mp4"
        match = re.match(pattern, segment_path.name)

        if not match:
            raise ValueError(
                f"Segment filename doesn't match expected pattern: {segment_path.name}"
            )

        date_str, time_str = match.groups()

        # Parse datetime from filename
        start_time = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
        end_time = start_time + timedelta(minutes=chunk_minutes)

        return start_time, end_time

    def find_segments_for_timerange(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Path]:
        """
        Find all segments that overlap with the given time range.

        Args:
            start_time: Start of desired clip
            end_time: End of desired clip

        Returns:
            List of segment paths that contain any part of the timerange, sorted chronologically
        """
        overlapping_segments = []

        for segment in self.buffer_dir.glob("segment_*.mp4"):
            try:
                seg_start, seg_end = self.get_segment_timerange(segment, self.chunk_minutes)

                # Check if segment overlaps with desired time range
                # Segments overlap if: seg_start < end_time AND seg_end > start_time
                if seg_start < end_time and seg_end > start_time:
                    overlapping_segments.append(segment)

            except ValueError as e:
                logger.warning(f"Skipping invalid segment file: {e}")
                continue

        # Sort chronologically by parsing filename
        return sorted(overlapping_segments)

    def extract_clip(
        self,
        start_time: datetime,
        duration_seconds: float,
        output_path: Path,
        fps: int | None = None,
        crf: int = 23,
    ) -> bool:
        """
        Extract clip from segments, handling multi-segment clips.

        Args:
            start_time: When clip should start
            duration_seconds: How long the clip should be
            output_path: Where to save the clip
            fps: Output framerate (uses self.fps if None)
            crf: Encoding quality (lower = better, 18-28 typical range)

        Returns:
            True if successful, False otherwise

        Implementation:
        - Finds segments needed for [start_time, start_time + duration]
        - Single segment: uses -ss and -t directly on that file
        - Multiple segments: creates concat demuxer file, then extracts
        - Calculates correct offset from first segment's start time
        """
        output_fps = fps if fps is not None else self.fps
        end_time = start_time + timedelta(seconds=duration_seconds)

        # Find segments that contain the clip
        segments = self.find_segments_for_timerange(start_time, end_time)

        if not segments:
            logger.error(f"No segments found for time range {start_time} to {end_time}")
            return False

        logger.info(f"Extracting clip from {len(segments)} segment(s): {start_time} to {end_time}")

        try:
            if len(segments) == 1:
                # Single segment: direct extraction
                segment = segments[0]
                seg_start, _ = self.get_segment_timerange(segment, self.chunk_minutes)
                offset = (start_time - seg_start).total_seconds()

                cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-ss",
                    str(offset),
                    "-i",
                    str(segment),
                    "-t",
                    str(duration_seconds),
                    "-c:v",
                    self.encoder,
                    "-crf",
                    str(crf),
                    "-r",
                    str(output_fps),
                    "-y",  # Overwrite output
                    str(output_path),
                ]

                logger.debug(f"Single segment extraction: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode != 0:
                    logger.error(f"ffmpeg extraction failed: {result.stderr}")
                    return False

                logger.info(f"Clip extracted successfully: {output_path}")
                return True

            else:
                # Multi-segment: use concat demuxer
                first_seg_start, _ = self.get_segment_timerange(segments[0], self.chunk_minutes)
                offset = (start_time - first_seg_start).total_seconds()

                # Create temporary concat file
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False
                ) as concat_file:
                    concat_path = Path(concat_file.name)
                    for segment in segments:
                        # Write file paths for concat demuxer
                        concat_file.write(f"file '{segment.absolute()}'\n")

                try:
                    cmd = [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_path),
                        "-ss",
                        str(offset),
                        "-t",
                        str(duration_seconds),
                        "-c:v",
                        self.encoder,
                        "-crf",
                        str(crf),
                        "-r",
                        str(output_fps),
                        "-y",  # Overwrite output
                        str(output_path),
                    ]

                    logger.debug(f"Multi-segment extraction: {' '.join(cmd)}")
                    result = subprocess.run(cmd, capture_output=True, text=True)

                    if result.returncode != 0:
                        logger.error(f"ffmpeg concat extraction failed: {result.stderr}")
                        return False

                    logger.info(f"Clip extracted from {len(segments)} segments: {output_path}")
                    return True

                finally:
                    # Cleanup temp file
                    if concat_path.exists():
                        concat_path.unlink()

        except Exception as e:
            logger.error(f"Failed to extract clip: {e}")
            return False

    def __enter__(self) -> "FFmpegTeeManager":
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.stop()
