"""Tests for clips module."""

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
