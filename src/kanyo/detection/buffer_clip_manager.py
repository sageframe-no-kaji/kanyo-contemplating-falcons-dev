"""
Buffer-based clip manager for falcon events.

Extracts clips from in-memory buffer and visit recordings.
No tee or segment files required - simple and reliable.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from kanyo.utils.logger import get_logger
from kanyo.utils.output import get_output_path

if TYPE_CHECKING:
    from kanyo.utils.frame_buffer import FrameBuffer
    from kanyo.utils.visit_recorder import VisitRecorder

logger = get_logger(__name__)


class BufferClipManager:
    """
    Manages clip extraction from frame buffer and visit recordings.

    This is a simpler alternative to the tee-based ClipManager.
    Clips are extracted from:
    - FrameBuffer: for pre-arrival footage and short clips
    - VisitRecorder: for in-visit footage (any length)

    Clip Strategy:
    - ARRIVAL: 15s before + 30s after (from visit recording)
    - DEPARTURE: 30s before + 15s after (from visit recording)
    - FULL VISIT: entire visit recording
    """

    def __init__(
        self,
        frame_buffer: FrameBuffer,
        visit_recorder: VisitRecorder,
        full_config: dict,
        clips_dir: str = "clips",
        clip_fps: int = 30,
        clip_crf: int = 23,
        # Arrival clip timing
        clip_arrival_before: int = 15,
        clip_arrival_after: int = 30,
        # Departure clip timing
        clip_departure_before: int = 30,
        clip_departure_after: int = 15,
    ):
        """
        Initialize buffer clip manager.

        Args:
            frame_buffer: FrameBuffer instance for pre-event footage
            visit_recorder: VisitRecorder instance for visit footage
            clips_dir: Base directory for saving clips
            clip_fps: Output FPS for clips
            clip_crf: CRF quality setting
            clip_arrival_before: Seconds before arrival to include
            clip_arrival_after: Seconds after arrival to include
            clip_departure_before: Seconds before departure to include
            clip_departure_after: Seconds after departure to include
        """
        self.frame_buffer = frame_buffer
        self.visit_recorder = visit_recorder
        self.full_config = full_config
        self.clips_dir = Path(clips_dir)
        self.clip_fps = clip_fps
        self.clip_crf = clip_crf

        self.clip_arrival_before = clip_arrival_before
        self.clip_arrival_after = clip_arrival_after
        self.clip_departure_before = clip_departure_before
        self.clip_departure_after = clip_departure_after

        # Async clip extraction
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clip_")
        self._shutdown = False

        logger.info("BufferClipManager initialized")

    def shutdown(self):
        """Shutdown the clip executor."""
        if self._shutdown:
            return
        self._shutdown = True
        logger.info("Shutting down buffer clip manager...")
        self._executor.shutdown(wait=True)
        logger.info("Buffer clip manager shutdown complete")

    def create_arrival_clip(self, visit_metadata: dict) -> bool:
        """
        Create arrival clip from visit recording.

        The arrival clip is the first N seconds of the visit recording,
        which includes lead-in from the buffer.

        Args:
            visit_metadata: Metadata from visit recorder with offsets

        Returns:
            True if scheduled successfully
        """
        visit_file = visit_metadata.get("visit_file")
        if not visit_file or not Path(visit_file).exists():
            logger.warning("Cannot create arrival clip: no visit file")
            return False

        visit_start = visit_metadata.get("visit_start")
        if not visit_start:
            return False

        # Parse timestamp if string
        if isinstance(visit_start, str):
            visit_start = datetime.fromisoformat(visit_start)

        # Arrival clip = first (before + after) seconds of recording
        clip_duration = self.clip_arrival_before + self.clip_arrival_after

        clip_path = get_output_path(
            str(self.clips_dir),
            visit_start,
            "arrival",
            "mp4",
        )

        logger.info(f"📹 Scheduling arrival clip: {clip_path.name}")

        self._executor.submit(
            self._extract_clip_from_visit,
            Path(visit_file),
            0,  # Start at beginning (includes lead-in)
            clip_duration,
            clip_path,
            "arrival",
        )
        return True

    def create_departure_clip(self, visit_metadata: dict) -> bool:
        """
        Create departure clip from visit recording.

        The departure clip is centered on the last detection time,
        NOT the end of the recording file.

        Args:
            visit_metadata: Metadata from visit recorder with offsets

        Returns:
            True if scheduled successfully
        """
        visit_file = visit_metadata.get("visit_file")
        if not visit_file or not Path(visit_file).exists():
            logger.warning("Cannot create departure clip: no visit file")
            return False

        visit_end = visit_metadata.get("visit_end")  # This is last_detection time
        recording_start = visit_metadata.get(
            "recording_start"
        )  # When file started (includes lead-in)

        if not visit_end or not recording_start:
            logger.warning("Cannot create departure clip: missing visit_end or recording_start")
            return False

        # Parse timestamps if strings
        if isinstance(visit_end, str):
            visit_end = datetime.fromisoformat(visit_end)
        if isinstance(recording_start, str):
            recording_start = datetime.fromisoformat(recording_start)

        # Calculate offset of last detection within the recording file
        last_detection_offset = (visit_end - recording_start).total_seconds()

        # Departure clip: centered on last detection
        # Start = last_detection - departure_before
        # Duration = departure_before + departure_after
        start_offset = max(0, last_detection_offset - self.clip_departure_before)
        clip_duration = self.clip_departure_before + self.clip_departure_after

        clip_path = get_output_path(
            str(self.clips_dir),
            visit_end,
            "departure",
            "mp4",
        )

        logger.info(f"📹 Scheduling departure clip: {clip_path.name}")
        logger.info(
            f"    Last detection at offset {last_detection_offset:.1f}s, "
            f"extracting {start_offset:.1f}s + {clip_duration}s"
        )

        self._executor.submit(
            self._extract_clip_from_visit,
            Path(visit_file),
            start_offset,
            clip_duration,
            clip_path,
            "departure",
        )
        return True

    def create_clip_from_buffer(
        self,
        event_time: datetime,
        event_name: str,
        before_seconds: float = 15,
        after_seconds: float = 30,
    ) -> bool:
        """
        Create a clip directly from the frame buffer.

        Used for initial detection clips when no visit recording exists yet.

        Args:
            event_time: Center point of the clip
            event_name: Type of event for filename
            before_seconds: Seconds before event to include
            after_seconds: Seconds after event to include

        Returns:
            True if clip creation was scheduled
        """
        start_time = event_time - timedelta(seconds=before_seconds)
        end_time = event_time + timedelta(seconds=after_seconds)

        clip_path = get_output_path(
            str(self.clips_dir),
            event_time,
            event_name,
            "mp4",
        )

        logger.info(f"📹 Extracting {event_name} clip from buffer: {clip_path.name}")

        self._executor.submit(
            self._extract_clip_from_buffer,
            start_time,
            end_time,
            clip_path,
            event_name,
        )
        return True

    def _extract_clip_from_buffer(
        self,
        start_time: datetime,
        end_time: datetime,
        clip_path: Path,
        clip_type: str,
    ) -> str | None:
        """Extract clip from frame buffer (runs in thread)."""
        try:
            success = self.frame_buffer.extract_clip(
                start_time=start_time,
                end_time=end_time,
                output_path=clip_path,
                fps=self.clip_fps,
                crf=self.clip_crf,
            )

            if success:
                logger.info(f"✅ {clip_type.capitalize()} clip saved: {clip_path}")
                return str(clip_path)
            else:
                logger.warning(f"Failed to create {clip_type} clip from buffer")
                return None

        except Exception as e:
            logger.error(f"Error creating {clip_type} clip: {e}")
            return None

    def _extract_clip_from_visit(
        self,
        visit_file: Path,
        start_offset: float,
        duration: float,
        clip_path: Path,
        clip_type: str,
    ) -> str | None:
        """Extract clip from visit file (runs in thread)."""
        try:
            from kanyo.utils.visit_recorder import VisitRecorder

            success = VisitRecorder.extract_clip_from_file(
                visit_file=visit_file,
                start_offset=start_offset,
                duration=duration,
                output_path=clip_path,
            )

            if success:
                logger.info(f"✅ {clip_type.capitalize()} clip saved: {clip_path}")
                return str(clip_path)
            else:
                logger.warning(f"Failed to create {clip_type} clip")
                return None

        except Exception as e:
            logger.error(f"Error creating {clip_type} clip: {e}")
            return None

    def create_standalone_arrival_clip(
        self,
        arrival_time: datetime,
        lead_in_frames: list,
        frame_size: tuple[int, int],
    ) -> tuple[Path, "VisitRecorder"] | tuple[None, None]:
        """
        Create arrival clip as standalone recording (not extracted from visit file).

        This records frames directly: buffer lead-in + next N seconds after arrival.
        Returns immediately with a recorder that needs frames written to it.

        Args:
            arrival_time: When falcon arrived
            lead_in_frames: Buffer frames before arrival
            frame_size: (width, height) of frames

        Returns:
            Tuple of (clip_path, recorder) or (None, None) if failed
        """
        from kanyo.utils.visit_recorder import VisitRecorder

        clip_path = get_output_path(
            str(self.clips_dir),
            arrival_time,
            "arrival",
            "mp4",
        )

        logger.info(f"📹 Creating standalone arrival clip: {clip_path.name}")

        # Create temporary recorder for the arrival clip
        temp_recorder = VisitRecorder(
            clips_dir=str(self.clips_dir.parent),  # Parent since we have full path
            fps=self.clip_fps,
            crf=self.clip_crf,
        )

        # Manually initialize recording (can't use start_recording() because it
        # overwrites _visit_path with get_output_path(..., "visit", ...)
        temp_recorder._visit_path = clip_path
        temp_recorder._visit_path.parent.mkdir(parents=True, exist_ok=True)
        temp_recorder._visit_start = arrival_time
        temp_recorder._recording_start = arrival_time - timedelta(
            seconds=temp_recorder.lead_in_seconds
        )
        temp_recorder._frame_count = 0
        temp_recorder._events = []
        temp_recorder._frame_size = frame_size

        # Start ffmpeg process
        width, height = frame_size
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "bgr24",
            "-r",
            str(temp_recorder.fps),
            "-i",
            "-",
        ]

        # Add encoder options
        if temp_recorder._encoder == "h264_videotoolbox":
            quality = max(1, min(100, int((51 - temp_recorder.crf) * 2)))
            cmd.extend(["-c:v", "h264_videotoolbox", "-q:v", str(quality)])
        elif temp_recorder._encoder == "h264_vaapi":
            cmd.extend(
                [
                    "-vaapi_device",
                    "/dev/dri/renderD128",
                    "-vf",
                    "format=nv12,hwupload",
                    "-c:v",
                    "h264_vaapi",
                    "-qp",
                    str(temp_recorder.crf),
                ]
            )
        elif temp_recorder._encoder == "h264_nvenc":
            cmd.extend(["-c:v", "h264_nvenc", "-cq", str(temp_recorder.crf)])
        else:
            cmd.extend(["-c:v", "libx264", "-crf", str(temp_recorder.crf), "-preset", "fast"])

        cmd.extend(["-movflags", "+faststart", str(clip_path)])

        logger.info(f"📹 Starting arrival clip recording: {clip_path}")

        try:
            stderr_log = clip_path.with_suffix(".ffmpeg.log")
            temp_recorder._stderr_file = open(stderr_log, "w")

            temp_recorder._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=temp_recorder._stderr_file,
            )
        except Exception as e:
            logger.error(f"Failed to start ffmpeg for arrival clip: {e}")
            if temp_recorder._stderr_file:
                temp_recorder._stderr_file.close()
                temp_recorder._stderr_file = None
            return None, None

        # Write lead-in frames
        if lead_in_frames:
            logger.info(f"Writing {len(lead_in_frames)} lead-in frames to arrival clip")
            for buffered_frame in lead_in_frames:
                frame = buffered_frame.decode()
                temp_recorder._write_raw_frame(frame)

        # Log arrival event
        temp_recorder._events.append(
            {
                "type": "arrival",
                "offset_seconds": 0,
                "timestamp": arrival_time.isoformat(),
            }
        )

        return clip_path, temp_recorder
