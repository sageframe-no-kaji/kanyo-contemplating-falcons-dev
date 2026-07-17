"""Tests for clips module."""

import io
import subprocess
from datetime import datetime
from unittest.mock import patch

import pytest

from kanyo.generation.clips import ClipEvent, ClipExtractor, ClipSpec


class TestClipsImports:
    """Verify clips module imports work."""

    def test_imports(self):
        """Verify clips module imports."""
        from kanyo.generation import clips

        assert clips is not None
        assert hasattr(clips, "ClipExtractor")
        assert hasattr(clips, "ClipEvent")
        assert hasattr(clips, "ClipSpec")

    def test_import_encoder_from_clips(self):
        """Verify detect_hardware_encoder is accessible from clips."""
        from kanyo.generation.clips import detect_hardware_encoder

        assert detect_hardware_encoder is not None


class TestClipEvent:
    """Tests for ClipEvent dataclass."""

    def test_creation(self):
        """ClipEvent can be created with required fields."""
        now = datetime.now()
        event = ClipEvent(
            event_type="enter",
            frame=7800,
            video_time_secs=130.0,
            timestamp=now,
        )

        assert event.event_type == "enter"
        assert event.frame == 7800
        assert event.video_time_secs == 130.0
        assert event.timestamp == now

    def test_exit_event(self):
        """ClipEvent works for exit events."""
        event = ClipEvent(
            event_type="exit",
            frame=32520,
            video_time_secs=542.0,
            timestamp=datetime.now(),
        )

        assert event.event_type == "exit"


class TestClipSpec:
    """Tests for ClipSpec dataclass."""

    def test_creation(self):
        """ClipSpec can be created."""
        spec = ClipSpec(
            start_secs=100.0,
            end_secs=190.0,
            event_type="enter",
            event_timestamp=datetime(2025, 12, 17, 10, 30, 0),
            first_event_time_secs=130.0,
        )

        assert spec.start_secs == 100.0
        assert spec.end_secs == 190.0
        assert spec.event_type == "enter"

    def test_duration_property(self):
        """ClipSpec duration calculates correctly."""
        spec = ClipSpec(
            start_secs=100.0,
            end_secs=190.0,
            event_type="enter",
            event_timestamp=datetime.now(),
            first_event_time_secs=130.0,
        )

        assert spec.duration_secs == 90.0

    def test_filename_property(self):
        """ClipSpec filename generates correctly."""
        spec = ClipSpec(
            start_secs=100.0,
            end_secs=190.0,
            event_type="enter",
            event_timestamp=datetime(2025, 12, 17, 10, 30, 45),
            first_event_time_secs=130.0,
        )

        assert spec.filename == "2025-12-17_10-30-45_enter.mp4"

    def test_thumbnail_filename_property(self):
        """ClipSpec thumbnail filename generates correctly."""
        spec = ClipSpec(
            start_secs=100.0,
            end_secs=190.0,
            event_type="exit",
            event_timestamp=datetime(2025, 12, 17, 14, 22, 33),
            first_event_time_secs=130.0,
        )

        assert spec.thumbnail_filename == "2025-12-17_14-22-33_exit.jpg"

    def test_merged_event_type(self):
        """ClipSpec supports merged event type."""
        spec = ClipSpec(
            start_secs=100.0,
            end_secs=600.0,
            event_type="merged",
            event_timestamp=datetime(2025, 12, 17, 10, 30, 0),
            first_event_time_secs=130.0,
            last_event_time_secs=540.0,
        )

        assert spec.event_type == "merged"
        assert spec.last_event_time_secs == 540.0
        assert spec.filename == "2025-12-17_10-30-00_merged.mp4"


class TestClipExtractor:
    """Tests for ClipExtractor class."""

    @pytest.fixture
    def config(self):
        """Sample config for tests."""
        return {
            "clips_dir": "clips",
            "clip_entrance_before": 30,
            "clip_entrance_after": 60,
            "clip_exit_before": 60,
            "clip_exit_after": 30,
            "clip_merge_threshold": 180,
            "clip_compress": True,
            "clip_crf": 23,
            "clip_fps": 30,
            "clip_hardware_encoding": True,
            "thumbnail_entrance_offset": 5,
            "thumbnail_exit_offset": -10,
        }

    @pytest.fixture
    def extractor(self, config, tmp_path):
        """Create ClipExtractor for tests."""
        video_path = tmp_path / "test_video.mp4"
        video_path.touch()
        return ClipExtractor(
            config=config,
            video_path=video_path,
            fps=60.0,
            video_duration_secs=900.0,  # 15 min
        )

    def test_instantiation(self, extractor):
        """ClipExtractor can be instantiated."""
        assert extractor is not None
        assert extractor.fps == 60.0

    def test_config_values_loaded(self, extractor):
        """Config values are loaded correctly."""
        assert extractor.arrival_before == 15
        assert extractor.arrival_after == 30
        assert extractor.departure_before == 30
        assert extractor.departure_after == 15
        assert extractor.merge_threshold == 180

    def test_add_event(self, extractor):
        """Events can be added."""
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        assert len(extractor.events) == 1
        assert extractor.events[0].event_type == "enter"
        assert extractor.events[0].frame == 7800
        assert extractor.events[0].video_time_secs == 130.0  # 7800/60

    def test_add_multiple_events(self, extractor):
        """Multiple events can be added."""
        now = datetime.now()
        extractor.add_event("enter", frame=7800, timestamp=now)
        extractor.add_event("exit", frame=32520, timestamp=now)

        assert len(extractor.events) == 2

    def test_plan_clips_empty(self, extractor):
        """No clips planned when no events."""
        clips = extractor.plan_clips()
        assert clips == []

    def test_plan_clips_single_enter(self, extractor):
        """Single enter event produces one clip."""
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        clips = extractor.plan_clips()

        assert len(clips) == 1
        assert clips[0].event_type == "enter"
        # 130s event - 15s before = 115s start
        assert clips[0].start_secs == 115.0
        # 130s event + 30s after = 160s end
        assert clips[0].end_secs == 160.0

    def test_plan_clips_single_exit(self, extractor):
        """Single exit event produces one clip."""
        extractor.add_event("exit", frame=32520, timestamp=datetime.now())

        clips = extractor.plan_clips()

        assert len(clips) == 1
        assert clips[0].event_type == "exit"
        # 542s event - 30s before = 512s start
        assert clips[0].start_secs == 512.0
        # 542s event + 15s after = 557s end
        assert clips[0].end_secs == 557.0

    def test_plan_clips_merge_close_events(self, extractor):
        """Close events are merged into single clip."""
        now = datetime.now()
        # Events 60 seconds apart (within 180s threshold)
        extractor.add_event("enter", frame=7800, timestamp=now)  # 130s
        extractor.add_event("exit", frame=11400, timestamp=now)  # 190s

        clips = extractor.plan_clips()

        assert len(clips) == 1
        assert clips[0].event_type == "merged"

    def test_plan_clips_no_merge_distant_events(self, extractor):
        """Distant events are not merged."""
        now = datetime.now()
        # Events 412s apart (outside 180s threshold)
        extractor.add_event("enter", frame=7800, timestamp=now)  # 130s
        extractor.add_event("exit", frame=32520, timestamp=now)  # 542s

        clips = extractor.plan_clips()

        assert len(clips) == 2
        assert clips[0].event_type == "enter"
        assert clips[1].event_type == "exit"

    def test_plan_clips_clamp_to_video_start(self, extractor):
        """Clip start is clamped to 0."""
        # Event near start of video
        extractor.add_event("enter", frame=600, timestamp=datetime.now())  # 10s

        clips = extractor.plan_clips()

        # 10s - 30s would be -20s, clamped to 0
        assert clips[0].start_secs == 0.0

    def test_plan_clips_clamp_to_video_end(self, extractor):
        """Clip end is clamped to video duration."""
        # Event near end of 900s video
        extractor.add_event("exit", frame=52800, timestamp=datetime.now())  # 880s

        clips = extractor.plan_clips()

        # 880s + 15s = 895s (departure_after default)
        assert clips[0].end_secs == 895.0

    def test_dry_run_returns_empty(self, extractor):
        """Dry run doesn't extract clips."""
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        with patch("subprocess.run") as mock_run:
            extractor.extract_clips(dry_run=True)

        # No ffmpeg called in dry run
        mock_run.assert_not_called()


class TestThumbnailFilenames:
    """Tests for thumbnail filename generation."""

    def test_thumbnail_filename_for_enter(self):
        """Custom thumbnail suffix for enter."""
        spec = ClipSpec(
            start_secs=100.0,
            end_secs=190.0,
            event_type="merged",
            event_timestamp=datetime(2025, 12, 17, 10, 30, 0),
            first_event_time_secs=130.0,
            last_event_time_secs=540.0,
        )

        assert spec.thumbnail_filename_for("enter") == "2025-12-17_10-30-00_enter.jpg"

    def test_thumbnail_filename_for_exit(self):
        """Custom thumbnail suffix for exit."""
        spec = ClipSpec(
            start_secs=100.0,
            end_secs=600.0,
            event_type="merged",
            event_timestamp=datetime(2025, 12, 17, 10, 30, 0),
            first_event_time_secs=130.0,
            last_event_time_secs=540.0,
        )

        assert spec.thumbnail_filename_for("exit") == "2025-12-17_10-30-00_exit.jpg"


class _FakeFFmpegProcess:
    """Stand-in for subprocess.Popen running ffmpeg with -progress pipe:1."""

    def __init__(self, lines=(), returncode=0, stderr_text=""):
        self.stdout = io.StringIO("".join(lines))
        self.stderr = io.StringIO(stderr_text)
        self.returncode = returncode

    def poll(self):
        return self.returncode


def _make_extractor(tmp_path, **config_overrides):
    """Build a ClipExtractor writing into tmp_path with a real (empty) video file."""
    config = {
        "clips_dir": str(tmp_path / "clips"),
        "clip_merge_threshold": 180,
        "clip_compress": True,
        "clip_crf": 23,
        "clip_fps": 30,
        "clip_encoder": "libx264",
        "thumbnail_entrance_offset": 5,
        "thumbnail_exit_offset": -10,
    }
    config.update(config_overrides)
    video_path = tmp_path / "test_video.mp4"
    video_path.touch()
    return ClipExtractor(
        config=config,
        video_path=video_path,
        fps=60.0,
        video_duration_secs=900.0,
    )


class TestExtractClips:
    """Behavior of extract_clips: ffmpeg command construction and error handling."""

    def test_no_events_logs_and_returns_empty(self, tmp_path):
        """With no events, extract_clips returns [] without touching ffmpeg."""
        extractor = _make_extractor(tmp_path)

        with patch("kanyo.generation.clips.subprocess.Popen") as mock_popen:
            result = extractor.extract_clips()

        assert result == []
        mock_popen.assert_not_called()

    def test_compress_libx264_builds_crf_command(self, tmp_path):
        """Software encoding uses CRF quality and returns the extracted clip path."""
        extractor = _make_extractor(tmp_path)
        extractor.add_event("enter", frame=7800, timestamp=datetime(2025, 12, 17, 10, 30, 0))

        process = _FakeFFmpegProcess()
        with (
            patch("kanyo.generation.clips.subprocess.Popen", return_value=process) as mock_popen,
            patch("kanyo.generation.clips.subprocess.run") as mock_run,
        ):
            result = extractor.extract_clips()

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-crf" in cmd
        assert cmd[cmd.index("-crf") + 1] == "23"
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert "-preset" in cmd
        # Output fps applied via -r for non-VAAPI encoders
        assert cmd[cmd.index("-r") + 1] == "30"
        expected_path = extractor.clips_dir / "2025-12-17_10-30-00_enter.mp4"
        assert result == [expected_path]
        # A thumbnail is extracted for the successful clip
        mock_run.assert_called_once()

    def test_compress_videotoolbox_quality_opts(self, tmp_path):
        """VideoToolbox maps CRF to -q:v on its 1-100 scale."""
        extractor = _make_extractor(tmp_path, clip_encoder="h264_videotoolbox")
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        with (
            patch(
                "kanyo.generation.clips.subprocess.Popen",
                return_value=_FakeFFmpegProcess(),
            ) as mock_popen,
            patch("kanyo.generation.clips.subprocess.run"),
        ):
            extractor.extract_clips()

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-c:v") + 1] == "h264_videotoolbox"
        # (51 - 23) * 2 = 56 on the VideoToolbox quality scale
        assert cmd[cmd.index("-q:v") + 1] == "56"

    def test_compress_vaapi_device_and_filter(self, tmp_path):
        """VAAPI adds the render device input option and hwupload filter."""
        extractor = _make_extractor(tmp_path, clip_encoder="h264_vaapi")
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        with (
            patch(
                "kanyo.generation.clips.subprocess.Popen",
                return_value=_FakeFFmpegProcess(),
            ) as mock_popen,
            patch("kanyo.generation.clips.subprocess.run"),
        ):
            extractor.extract_clips()

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
        assert cmd[cmd.index("-vf") + 1] == "format=nv12,hwupload,fps=30"
        assert "-qp" in cmd

    def test_compress_nvenc_uses_cq(self, tmp_path):
        """Non-special hardware encoders (NVENC/QSV/AMF) use -cq quality."""
        extractor = _make_extractor(tmp_path, clip_encoder="h264_nvenc")
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        with (
            patch(
                "kanyo.generation.clips.subprocess.Popen",
                return_value=_FakeFFmpegProcess(),
            ) as mock_popen,
            patch("kanyo.generation.clips.subprocess.run"),
        ):
            extractor.extract_clips()

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-cq") + 1] == "23"

    def test_auto_hardware_detection(self, tmp_path):
        """encoder='auto' with hardware encoding on calls detect_hardware_encoder."""
        extractor = _make_extractor(tmp_path, clip_encoder="auto", clip_hardware_encoding=True)
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        with (
            patch(
                "kanyo.generation.clips.detect_hardware_encoder",
                return_value="h264_videotoolbox",
            ) as mock_detect,
            patch(
                "kanyo.generation.clips.subprocess.Popen",
                return_value=_FakeFFmpegProcess(),
            ) as mock_popen,
            patch("kanyo.generation.clips.subprocess.run"),
        ):
            extractor.extract_clips()

        mock_detect.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-c:v") + 1] == "h264_videotoolbox"

    def test_software_fallback_when_hardware_disabled(self, tmp_path):
        """clip_hardware_encoding=False forces libx264 without detection."""
        extractor = _make_extractor(tmp_path, clip_encoder=None, clip_hardware_encoding=False)
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        with (
            patch("kanyo.generation.clips.detect_hardware_encoder") as mock_detect,
            patch(
                "kanyo.generation.clips.subprocess.Popen",
                return_value=_FakeFFmpegProcess(),
            ) as mock_popen,
            patch("kanyo.generation.clips.subprocess.run"),
        ):
            extractor.extract_clips()

        mock_detect.assert_not_called()
        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-c:v") + 1] == "libx264"

    def test_progress_output_parsed(self, tmp_path, capsys):
        """ffmpeg -progress lines are parsed into a percentage; bad lines ignored."""
        extractor = _make_extractor(tmp_path)
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        # Enter clip is 45s (15 before + 30 after); 10s in = 22%
        process = _FakeFFmpegProcess(
            lines=[
                "frame=100\n",
                "out_time_ms=10000000\n",  # 10s, > 5s threshold -> printed
                "out_time_ms=garbage\n",  # unparseable -> silently ignored
                "out_time_ms=12000000\n",  # only +2s since last -> not printed
            ]
        )
        with (
            patch("kanyo.generation.clips.subprocess.Popen", return_value=process),
            patch("kanyo.generation.clips.subprocess.run"),
        ):
            result = extractor.extract_clips()

        assert len(result) == 1
        out = capsys.readouterr().out
        assert "Encoding: 22%" in out
        assert "10s / 45s" in out

    def test_ffmpeg_failure_skips_clip(self, tmp_path):
        """Nonzero ffmpeg exit is logged; clip is skipped, no thumbnail attempted."""
        extractor = _make_extractor(tmp_path)
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        process = _FakeFFmpegProcess(returncode=1, stderr_text="encode blew up")
        with (
            patch("kanyo.generation.clips.subprocess.Popen", return_value=process),
            patch("kanyo.generation.clips.subprocess.run") as mock_run,
        ):
            result = extractor.extract_clips()

        assert result == []
        mock_run.assert_not_called()

    def test_ffmpeg_not_installed_aborts_loop(self, tmp_path):
        """FileNotFoundError (no ffmpeg) stops extraction after the first attempt."""
        extractor = _make_extractor(tmp_path)
        now = datetime.now()
        # Two events far apart -> two planned clips
        extractor.add_event("enter", frame=7800, timestamp=now)  # 130s
        extractor.add_event("exit", frame=32520, timestamp=now)  # 542s

        with patch(
            "kanyo.generation.clips.subprocess.Popen",
            side_effect=FileNotFoundError("ffmpeg"),
        ) as mock_popen:
            result = extractor.extract_clips()

        assert result == []
        # Break after the first failure, not one attempt per clip
        assert mock_popen.call_count == 1

    def test_copy_mode_uses_stream_copy(self, tmp_path):
        """clip_compress=False re-muxes with -c copy via subprocess.run."""
        extractor = _make_extractor(tmp_path, clip_compress=False)
        extractor.add_event("enter", frame=7800, timestamp=datetime(2025, 12, 17, 10, 30, 0))

        with patch("kanyo.generation.clips.subprocess.run") as mock_run:
            result = extractor.extract_clips()

        # First call is the clip copy, second the thumbnail
        assert mock_run.call_count == 2
        clip_cmd = mock_run.call_args_list[0][0][0]
        assert clip_cmd[clip_cmd.index("-c") + 1] == "copy"
        assert "-avoid_negative_ts" in clip_cmd
        expected_path = extractor.clips_dir / "2025-12-17_10-30-00_enter.mp4"
        assert result == [expected_path]

    def test_copy_mode_failure_returns_empty(self, tmp_path):
        """CalledProcessError in copy mode is caught; clip not reported extracted."""
        extractor = _make_extractor(tmp_path, clip_compress=False)
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())

        with patch(
            "kanyo.generation.clips.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"], stderr="bad input"),
        ):
            result = extractor.extract_clips()

        assert result == []

    def test_output_directory_created(self, tmp_path):
        """extract_clips creates the clips directory before writing."""
        extractor = _make_extractor(tmp_path)
        extractor.add_event("enter", frame=7800, timestamp=datetime.now())
        assert not extractor.clips_dir.exists()

        with (
            patch(
                "kanyo.generation.clips.subprocess.Popen",
                return_value=_FakeFFmpegProcess(),
            ),
            patch("kanyo.generation.clips.subprocess.run"),
        ):
            extractor.extract_clips()

        assert extractor.clips_dir.is_dir()


class TestExtractThumbnail:
    """Behavior of _extract_thumbnail across event types."""

    @staticmethod
    def _spec(event_type, first_time=130.0, last_time=None, start=115.0, end=160.0):
        return ClipSpec(
            start_secs=start,
            end_secs=end,
            event_type=event_type,
            event_timestamp=datetime(2025, 12, 17, 10, 30, 0),
            first_event_time_secs=first_time,
            last_event_time_secs=last_time,
        )

    def test_enter_thumbnail_offset(self, tmp_path):
        """Enter thumbnails are taken entrance_offset seconds after the event."""
        extractor = _make_extractor(tmp_path)
        extractor.clips_dir.mkdir(parents=True)
        spec = self._spec("enter")

        with patch("kanyo.generation.clips.subprocess.run") as mock_run:
            thumbs = extractor._extract_thumbnail(spec)

        cmd = mock_run.call_args[0][0]
        # 130s event + 5s offset = 135s, within [115, 160]
        assert cmd[cmd.index("-ss") + 1] == "135.0"
        assert cmd[cmd.index("-vframes") + 1] == "1"
        assert thumbs == [extractor.clips_dir / "2025-12-17_10-30-00_enter.jpg"]

    def test_exit_thumbnail_offset(self, tmp_path):
        """Exit thumbnails are taken exit_offset seconds before the event."""
        extractor = _make_extractor(tmp_path)
        extractor.clips_dir.mkdir(parents=True)
        spec = self._spec("exit", first_time=542.0, start=512.0, end=557.0)

        with patch("kanyo.generation.clips.subprocess.run") as mock_run:
            thumbs = extractor._extract_thumbnail(spec)

        cmd = mock_run.call_args[0][0]
        # 542s event - 10s offset = 532s
        assert cmd[cmd.index("-ss") + 1] == "532.0"
        assert thumbs == [extractor.clips_dir / "2025-12-17_10-30-00_exit.jpg"]

    def test_merged_produces_enter_and_exit_thumbnails(self, tmp_path):
        """Merged clips yield two thumbnails: entrance and exit."""
        extractor = _make_extractor(tmp_path)
        extractor.clips_dir.mkdir(parents=True)
        spec = self._spec("merged", first_time=130.0, last_time=190.0, end=220.0)

        with patch("kanyo.generation.clips.subprocess.run") as mock_run:
            thumbs = extractor._extract_thumbnail(spec)

        assert mock_run.call_count == 2
        enter_cmd = mock_run.call_args_list[0][0][0]
        exit_cmd = mock_run.call_args_list[1][0][0]
        assert enter_cmd[enter_cmd.index("-ss") + 1] == "135.0"  # 130 + 5
        assert exit_cmd[exit_cmd.index("-ss") + 1] == "180.0"  # 190 - 10
        assert thumbs == [
            extractor.clips_dir / "2025-12-17_10-30-00_enter.jpg",
            extractor.clips_dir / "2025-12-17_10-30-00_exit.jpg",
        ]

    def test_thumbnail_time_clamped_to_clip_bounds(self, tmp_path):
        """Thumbnail time is clamped into [start, end] of the clip."""
        extractor = _make_extractor(tmp_path)
        extractor.clips_dir.mkdir(parents=True)
        # Event at 158s + 5s offset = 163s, past the 160s clip end -> clamp to 160
        spec = self._spec("enter", first_time=158.0)

        with patch("kanyo.generation.clips.subprocess.run") as mock_run:
            extractor._extract_thumbnail(spec)

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("-ss") + 1] == "160.0"

    def test_dry_run_extracts_nothing(self, tmp_path):
        """Dry run logs intent without invoking ffmpeg."""
        extractor = _make_extractor(tmp_path)
        spec = self._spec("merged", first_time=130.0, last_time=190.0, end=220.0)

        with patch("kanyo.generation.clips.subprocess.run") as mock_run:
            thumbs = extractor._extract_thumbnail(spec, dry_run=True)

        assert thumbs == []
        mock_run.assert_not_called()

    def test_ffmpeg_failure_yields_no_thumbnail(self, tmp_path):
        """A failing ffmpeg call is logged and the thumbnail skipped."""
        extractor = _make_extractor(tmp_path)
        extractor.clips_dir.mkdir(parents=True)
        spec = self._spec("enter")

        with patch(
            "kanyo.generation.clips.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"], stderr="no frame"),
        ):
            thumbs = extractor._extract_thumbnail(spec)

        assert thumbs == []


class TestExtractFromVisit:
    """Behavior of the extract_from_visit convenience wrapper."""

    def test_replaces_existing_events(self, tmp_path):
        """extract_from_visit clears prior events and adds enter + exit."""
        extractor = _make_extractor(tmp_path)
        extractor.add_event("enter", frame=100, timestamp=datetime.now())

        now = datetime.now()
        result = extractor.extract_from_visit(
            enter_frame=7800,
            exit_frame=11400,
            enter_timestamp=now,
            exit_timestamp=now,
            dry_run=True,
        )

        assert result == []
        assert len(extractor.events) == 2
        assert extractor.events[0].event_type == "enter"
        assert extractor.events[0].frame == 7800
        assert extractor.events[1].event_type == "exit"
        assert extractor.events[1].frame == 11400

    def test_close_visit_merges_into_single_clip(self, tmp_path):
        """A short visit (within merge_threshold) plans one merged clip."""
        extractor = _make_extractor(tmp_path)
        now = datetime.now()
        extractor.extract_from_visit(
            enter_frame=7800,  # 130s
            exit_frame=11400,  # 190s -> 60s gap, within 180s threshold
            enter_timestamp=now,
            exit_timestamp=now,
            dry_run=True,
        )

        clips = extractor.plan_clips()
        assert len(clips) == 1
        assert clips[0].event_type == "merged"
