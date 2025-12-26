"""
Clip extraction for falcon detection events.

Extracts video clips around detected entrance/exit events using ffmpeg.
Handles merging of close events into single clips.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from kanyo.utils.encoder import detect_hardware_encoder
from kanyo.utils.logger import get_logger

if TYPE_CHECKING:
    from typing import Literal

logger = get_logger(__name__)


@dataclass
class ClipEvent:
    """A detected event with timing info for clip extraction."""

    event_type: Literal["enter", "exit"]
    frame: int
    video_time_secs: float
    timestamp: datetime  # For filename


@dataclass
class ClipSpec:
    """Specification for a clip to extract."""

    start_secs: float
    end_secs: float
    event_type: Literal["enter", "exit", "merged"]
    event_timestamp: datetime
    first_event_time_secs: float  # Video time of first event (for thumbnail)
    last_event_time_secs: float | None = (
        None  # Video time of last event (for merged exit thumbnail)
    )

    @property
    def duration_secs(self) -> float:
        return self.end_secs - self.start_secs

    @property
    def filename(self) -> str:
        """Generate filename: YYYY-MM-DD_HH-MM-SS_[type].mp4"""
        ts = self.event_timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        return f"{ts}_{self.event_type}.mp4"

    @property
    def thumbnail_filename(self) -> str:
        """Generate thumbnail filename: YYYY-MM-DD_HH-MM-SS_[type].jpg"""
        ts = self.event_timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        return f"{ts}_{self.event_type}.jpg"

    def thumbnail_filename_for(self, suffix: str) -> str:
        """Generate thumbnail filename with custom suffix."""
        ts = self.event_timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        return f"{ts}_{suffix}.jpg"


class ClipExtractor:
    """
    Extracts video clips around detected events.

    Usage:
        extractor = ClipExtractor(config, video_path, fps=60)
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())
        extractor.add_event("exit", frame=32520, timestamp=datetime.now())
        extractor.extract_clips()
    """

    def __init__(
        self,
        config: dict,
        video_path: str | Path,
        fps: float = 60.0,
        video_duration_secs: float | None = None,
    ):
        self.config = config
        self.video_path = Path(video_path)
        self.fps = fps
        self.video_duration_secs = video_duration_secs

        # Config values
        self.clips_dir = Path(config.get("clips_dir", "clips"))
        self.arrival_before = config.get("clip_arrival_before", 15)
        self.arrival_after = config.get("clip_arrival_after", 30)
        self.departure_before = config.get("clip_departure_before", 30)
        self.departure_after = config.get("clip_departure_after", 15)
        self.merge_threshold = config.get("clip_merge_threshold", 180)

        # Thumbnail offsets
        self.thumbnail_entrance_offset = config.get("thumbnail_entrance_offset", 5)
        self.thumbnail_exit_offset = config.get("thumbnail_exit_offset", -10)

        # TODO: Implement continuous recording mode - save entire stream
        # in 6-hour chunks instead of event clips
        self.continuous_recording = config.get("continuous_recording", False)
        self.continuous_chunk_hours = config.get("continuous_chunk_hours", 6)

        self.events: list[ClipEvent] = []

    def add_event(
        self,
        event_type: Literal["enter", "exit"],
        frame: int,
        timestamp: datetime,
    ) -> None:
        """Add a detected event for clip extraction."""
        video_time = frame / self.fps
        self.events.append(
            ClipEvent(
                event_type=event_type,
                frame=frame,
                video_time_secs=video_time,
                timestamp=timestamp,
            )
        )

    def _calculate_clip_bounds(self, event: ClipEvent) -> tuple[float, float]:
        """Calculate start/end times for a single event clip."""
        if event.event_type == "enter":
            start = event.video_time_secs - self.arrival_before
            end = event.video_time_secs + self.arrival_after
        else:  # exit
            start = event.video_time_secs - self.departure_before
            end = event.video_time_secs + self.departure_after

        # Clamp to video bounds
        start = max(0, start)
        if self.video_duration_secs:
            end = min(end, self.video_duration_secs)

        return start, end

    def plan_clips(self) -> list[ClipSpec]:
        """
        Plan clips from events, merging close events.

        Returns list of ClipSpec objects ready for extraction.
        """
        if not self.events:
            return []

        # Sort events by video time
        sorted_events = sorted(self.events, key=lambda e: e.video_time_secs)

        clips: list[ClipSpec] = []
        i = 0

        while i < len(sorted_events):
            event = sorted_events[i]
            start, end = self._calculate_clip_bounds(event)
            event_type = event.event_type
            event_timestamp = event.timestamp
            first_event_time = event.video_time_secs  # For thumbnail positioning
            last_event_time = event.video_time_secs  # Will be updated if merged
            final_event_type: Literal["enter", "exit", "merged"] = event_type

            # Check if next event should be merged
            j = i + 1
            while j < len(sorted_events):
                next_event = sorted_events[j]
                time_gap = next_event.video_time_secs - event.video_time_secs

                if time_gap <= self.merge_threshold:
                    # Merge: extend clip to cover next event
                    _, next_end = self._calculate_clip_bounds(next_event)
                    end = max(end, next_end)
                    final_event_type = "merged"
                    last_event_time = next_event.video_time_secs
                    j += 1
                else:
                    break

            clips.append(
                ClipSpec(
                    start_secs=start,
                    end_secs=end,
                    event_type=final_event_type,
                    event_timestamp=event_timestamp,
                    first_event_time_secs=first_event_time,
                    last_event_time_secs=last_event_time if event_type == "merged" else None,
                )
            )

            i = j  # Skip merged events

        return clips

    def extract_clips(self, dry_run: bool = False) -> list[Path]:
        """
        Extract all planned clips using ffmpeg.

        Args:
            dry_run: If True, just log what would be done without extracting.

        Returns:
            List of paths to extracted clip files.
        """
        clips = self.plan_clips()

        if not clips:
            logger.info("No clips to extract")
            return []

        # Ensure output directory exists
        self.clips_dir.mkdir(parents=True, exist_ok=True)

        extracted: list[Path] = []

        for clip in clips:
            output_path = self.clips_dir / clip.filename

            logger.info(
                f"{'[DRY RUN] ' if dry_run else ''}"
                f"Extracting {clip.event_type} clip: {clip.filename} "
                f"({clip.start_secs:.1f}s - {clip.end_secs:.1f}s, "
                f"duration: {clip.duration_secs:.1f}s)"
            )

            if dry_run:
                continue

            # Use ffmpeg to extract clip
            # -ss before -i for fast seeking
            # -t for duration
            compress = self.config.get("clip_compress", True)
            crf = self.config.get("clip_crf", 23)
            output_fps = self.config.get("clip_fps", 30)

            # Get encoder: check hardware_encoding boolean first, then explicit encoder
            use_hardware = self.config.get("clip_hardware_encoding", True)
            encoder = self.config.get("clip_encoder")

            if encoder and encoder != "auto":
                # Explicit encoder specified
                pass
            elif use_hardware:
                # Auto-detect hardware encoder
                encoder = detect_hardware_encoder()
            else:
                # Software encoding
                encoder = "libx264"

            if compress:
                # Build encoder-specific options
                if encoder == "h264_videotoolbox":
                    # Mac VideoToolbox uses -q:v (1-100, higher=better)
                    quality_opts = ["-q:v", str(max(1, min(100, int((51 - crf) * 2))))]
                    input_opts = []
                elif encoder == "h264_vaapi":
                    # VAAPI needs device and hwupload filter
                    quality_opts = ["-qp", str(crf)]
                    input_opts = ["-vaapi_device", "/dev/dri/renderD128"]
                elif encoder == "libx264":
                    # Software uses CRF directly
                    quality_opts = ["-crf", str(crf), "-preset", "fast"]
                    input_opts = []
                else:
                    # NVENC/QSV/AMF use -cq or -global_quality
                    quality_opts = ["-cq", str(crf)]
                    input_opts = []

                # VAAPI needs a filter to upload to GPU
                if encoder == "h264_vaapi":
                    vf_opts = ["-vf", f"format=nv12,hwupload,fps={output_fps}"]
                else:
                    vf_opts = ["-r", str(output_fps)]

                cmd = [
                    "ffmpeg",
                    "-y",  # Overwrite output
                    *input_opts,
                    "-ss",
                    str(clip.start_secs),
                    "-i",
                    str(self.video_path),
                    "-t",
                    str(clip.duration_secs),
                    *vf_opts,
                    "-c:v",
                    encoder,
                    *quality_opts,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    "-progress",
                    "pipe:1",  # Show progress
                    str(output_path),
                ]
            else:
                # Fast copy without re-encoding (larger files)
                cmd = [
                    "ffmpeg",
                    "-y",  # Overwrite output
                    "-ss",
                    str(clip.start_secs),
                    "-i",
                    str(self.video_path),
                    "-t",
                    str(clip.duration_secs),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    "-avoid_negative_ts",
                    "make_zero",
                    str(output_path),
                ]

            try:
                if compress:
                    # Run with progress output
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )

                    # Parse progress from ffmpeg
                    last_time = 0.0
                    assert process.stdout is not None
                    while True:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line.startswith("out_time_ms="):
                            try:
                                time_ms = int(line.split("=")[1].strip())
                                time_secs = time_ms / 1_000_000
                                if time_secs > last_time + 5:  # Update every 5s
                                    pct = min(100, int(time_secs / clip.duration_secs * 100))
                                    msg = f"  ⏳ Encoding: {pct}%"
                                    msg += f" ({time_secs:.0f}s / {clip.duration_secs:.0f}s)"
                                    print(msg, end="\r")
                                    last_time = time_secs
                            except (ValueError, IndexError):
                                pass

                    print()  # Clear progress line
                    if process.returncode != 0:
                        assert process.stderr is not None
                        stderr = process.stderr.read()
                        raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr)
                else:
                    # Fast copy - no progress needed
                    subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        check=True,
                    )

                extracted.append(output_path)
                logger.info(f"✅ Saved: {output_path}")

                # Extract thumbnail for this clip
                self._extract_thumbnail(clip, dry_run=False)

            except subprocess.CalledProcessError as e:
                logger.error(f"❌ ffmpeg failed: {e.stderr if hasattr(e, 'stderr') else e}")
            except FileNotFoundError:
                logger.error("❌ ffmpeg not found. Install with: brew install ffmpeg")
                break

        return extracted

    def _extract_thumbnail(self, clip: ClipSpec, dry_run: bool = False) -> list[Path]:
        """
        Extract thumbnail(s) for a clip.

        For enter events: 1 thumbnail at entrance + offset
        For exit events: 1 thumbnail at exit + offset
        For merged events: 2 thumbnails (entrance + offset, exit + offset)

        Returns list of extracted thumbnail paths.
        """
        thumbnails: list[Path] = []

        if clip.event_type == "merged" and clip.last_event_time_secs is not None:
            # Merged clip: extract 2 thumbnails
            # 1. Entrance thumbnail (5s after entrance)
            enter_time = clip.first_event_time_secs + self.thumbnail_entrance_offset
            enter_time = max(clip.start_secs, min(enter_time, clip.end_secs))
            enter_path = self.clips_dir / clip.thumbnail_filename_for("enter")

            # 2. Exit thumbnail (10s before exit)
            exit_time = clip.last_event_time_secs + self.thumbnail_exit_offset
            exit_time = max(clip.start_secs, min(exit_time, clip.end_secs))
            exit_path = self.clips_dir / clip.thumbnail_filename_for("exit")

            thumb_specs = [
                (enter_time, enter_path, "enter"),
                (exit_time, exit_path, "exit"),
            ]
        elif clip.event_type == "enter":
            thumb_time = clip.first_event_time_secs + self.thumbnail_entrance_offset
            thumb_time = max(clip.start_secs, min(thumb_time, clip.end_secs))
            thumb_specs = [(thumb_time, self.clips_dir / clip.thumbnail_filename, "enter")]
        else:  # exit
            thumb_time = clip.first_event_time_secs + self.thumbnail_exit_offset
            thumb_time = max(clip.start_secs, min(thumb_time, clip.end_secs))
            thumb_specs = [(thumb_time, self.clips_dir / clip.thumbnail_filename, "exit")]

        for thumb_time, output_path, label in thumb_specs:
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would extract {label} thumbnail at "
                    f"{thumb_time:.1f}s: {output_path.name}"
                )
                continue

            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(thumb_time),
                "-i",
                str(self.video_path),
                "-vframes",
                "1",
                "-q:v",
                "2",
                str(output_path),
            ]

            try:
                subprocess.run(cmd, capture_output=True, text=True, check=True)
                logger.info(f"📸 Thumbnail: {output_path}")
                thumbnails.append(output_path)
            except subprocess.CalledProcessError as e:
                logger.error(f"❌ Thumbnail failed: {e.stderr}")

        return thumbnails

    def extract_from_visit(
        self,
        enter_frame: int,
        exit_frame: int,
        enter_timestamp: datetime,
        exit_timestamp: datetime,
        dry_run: bool = False,
    ) -> list[Path]:
        """
        Convenience method to extract clips for a complete visit.

        Automatically determines whether to create separate enter/exit clips
        or merge into a single clip based on merge_threshold.
        """
        self.events.clear()
        self.add_event("enter", enter_frame, enter_timestamp)
        self.add_event("exit", exit_frame, exit_timestamp)
        return self.extract_clips(dry_run=dry_run)
