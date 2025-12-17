"""Tests for live_tee module."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from kanyo.utils.live_tee import FFmpegTeeManager


class TestFFmpegTeeManager:
    """Tests for FFmpegTeeManager class."""

    @pytest.fixture
    def tee_manager(self, tmp_path):
        """Create tee manager for testing."""
        return FFmpegTeeManager(
            stream_url="https://test.url/stream",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=tmp_path / "buffer",
            chunk_minutes=10,
            encoder="libx264",  # Use software for tests
        )

    def test_instantiation(self, tee_manager):
        """FFmpegTeeManager can be instantiated."""
        assert tee_manager is not None
        assert tee_manager.encoder == "libx264"
        assert tee_manager.chunk_minutes == 10

    def test_buffer_dir_created(self, tmp_path):
        """Buffer directory is created on init."""
        buffer_dir = tmp_path / "new_buffer"
        assert not buffer_dir.exists()

        tee_manager = FFmpegTeeManager(
            stream_url="https://test.url",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=buffer_dir,
        )

        assert buffer_dir.exists()
        assert tee_manager.buffer_dir == buffer_dir

    def test_build_command_libx264(self, tee_manager):
        """Build command uses libx264 fallback."""
        cmd = tee_manager.build_command()

        assert "ffmpeg" in cmd
        assert "-i" in cmd
        assert "https://test.url/stream" in cmd
        assert "udp://127.0.0.1:12345" in cmd
        assert "-c:v" in cmd
        assert "libx264" in cmd
        assert "-f" in cmd
        assert "segment" in cmd

    def test_build_command_vaapi(self, tmp_path):
        """Build command for VAAPI encoder."""
        tee_manager = FFmpegTeeManager(
            stream_url="https://test.url",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=tmp_path,
            encoder="h264_vaapi",
        )

        cmd = tee_manager.build_command()

        assert "h264_vaapi" in cmd
        assert "-vaapi_device" in cmd
        assert "/dev/dri/renderD128" in cmd
        assert "format=nv12,hwupload" in cmd

    def test_build_command_videotoolbox(self, tmp_path):
        """Build command for VideoToolbox encoder."""
        tee_manager = FFmpegTeeManager(
            stream_url="https://test.url",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=tmp_path,
            encoder="h264_videotoolbox",
        )

        cmd = tee_manager.build_command()

        assert "h264_videotoolbox" in cmd
        assert "-crf" in cmd

    def test_build_command_nvenc(self, tmp_path):
        """Build command for NVENC encoder."""
        tee_manager = FFmpegTeeManager(
            stream_url="https://test.url",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=tmp_path,
            encoder="h264_nvenc",
        )

        cmd = tee_manager.build_command()

        assert "h264_nvenc" in cmd
        assert "-preset" in cmd
        assert "fast" in cmd

    def test_build_command_custom_fps(self, tmp_path):
        """Build command with custom framerate."""
        tee_manager = FFmpegTeeManager(
            stream_url="https://test.url",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=tmp_path,
            encoder="libx264",
            fps=15,
        )

        cmd = tee_manager.build_command()

        # Check that the framerate is set correctly
        assert "-r" in cmd
        r_index = cmd.index("-r")
        assert cmd[r_index + 1] == "15"

    def test_build_command_default_fps(self, tmp_path):
        """Build command uses default 30fps when not specified."""
        tee_manager = FFmpegTeeManager(
            stream_url="https://test.url",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=tmp_path,
            encoder="libx264",
        )

        cmd = tee_manager.build_command()

        # Check that default framerate is 30
        assert "-r" in cmd
        r_index = cmd.index("-r")
        assert cmd[r_index + 1] == "30"

    def test_is_running_false_when_not_started(self, tee_manager):
        """is_running returns False when not started."""
        assert not tee_manager.is_running()

    @patch("subprocess.Popen")
    def test_start_creates_process(self, mock_popen, tee_manager):
        """Start creates subprocess."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Still running
        mock_popen.return_value = mock_process

        result = tee_manager.start()

        assert result is True
        assert tee_manager.process is mock_process
        mock_popen.assert_called_once()

    @patch("subprocess.Popen")
    def test_start_fails_if_process_dies(self, mock_popen, tee_manager):
        """Start fails if process dies immediately."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 1  # Exited with error
        mock_process.stderr.read.return_value = "test error"
        mock_popen.return_value = mock_process

        result = tee_manager.start()

        assert result is False
        assert tee_manager.process is None

    def test_stop_without_start(self, tee_manager):
        """Stop does nothing if not started."""
        tee_manager.stop()  # Should not raise

    @patch("subprocess.Popen")
    def test_stop_terminates_process(self, mock_popen, tee_manager):
        """Stop terminates running process."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        tee_manager.start()
        tee_manager.stop()

        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called()

    def test_get_recent_segments(self, tmp_path):
        """Get recent segments returns sorted files."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create test segment files
        (buffer_dir / "segment_20251217_100000.mp4").touch()
        (buffer_dir / "segment_20251217_101000.mp4").touch()
        (buffer_dir / "segment_20251217_102000.mp4").touch()

        tee_manager = FFmpegTeeManager(
            stream_url="https://test.url",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=buffer_dir,
        )

        segments = tee_manager.get_recent_segments(limit=2)

        assert len(segments) == 2
        # Should be newest first
        assert "102000" in segments[0].name
        assert "101000" in segments[1].name

    def test_cleanup_old_segments(self, tmp_path):
        """Cleanup removes old segments."""
        import time

        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create old segment
        old_file = buffer_dir / "segment_old.mp4"
        old_file.touch()
        # Set modification time to 2 hours ago
        old_time = time.time() - (2 * 3600)
        old_file.touch()  # Create file first
        import os

        os.utime(old_file, (old_time, old_time))

        # Create recent segment
        new_file = buffer_dir / "segment_new.mp4"
        new_file.touch()

        tee_manager = FFmpegTeeManager(
            stream_url="https://test.url",
            proxy_url="udp://127.0.0.1:12345",
            buffer_dir=buffer_dir,
        )

        deleted = tee_manager.cleanup_old_segments(keep_minutes=60)

        assert deleted == 1
        assert not old_file.exists()
        assert new_file.exists()


class TestSegmentTimerangeAndExtraction:
    """Tests for segment timerange parsing and clip extraction."""

    def test_get_segment_timerange_valid_filename(self):
        """Parse valid segment filename correctly."""
        segment = Path("segment_20231217_143000.mp4")
        start, end = FFmpegTeeManager.get_segment_timerange(segment, chunk_minutes=10)

        assert start == datetime(2023, 12, 17, 14, 30, 0)
        assert end == datetime(2023, 12, 17, 14, 40, 0)

    def test_get_segment_timerange_custom_duration(self):
        """Parse segment with custom chunk duration."""
        segment = Path("segment_20231217_120000.mp4")
        start, end = FFmpegTeeManager.get_segment_timerange(segment, chunk_minutes=5)

        assert start == datetime(2023, 12, 17, 12, 0, 0)
        assert end == datetime(2023, 12, 17, 12, 5, 0)

    def test_get_segment_timerange_invalid_filename(self):
        """Invalid filename raises ValueError."""
        segment = Path("invalid_filename.mp4")

        with pytest.raises(ValueError, match="doesn't match expected pattern"):
            FFmpegTeeManager.get_segment_timerange(segment)

    def test_get_segment_timerange_edge_of_day(self):
        """Segment crossing midnight boundary."""
        segment = Path("segment_20231217_235500.mp4")
        start, end = FFmpegTeeManager.get_segment_timerange(segment, chunk_minutes=10)

        assert start == datetime(2023, 12, 17, 23, 55, 0)
        assert end == datetime(2023, 12, 18, 0, 5, 0)  # Next day

    def test_find_segments_for_timerange_single_segment(self, tmp_path):
        """Find single segment containing entire clip."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create segment: 14:30:00 - 14:40:00
        (buffer_dir / "segment_20231217_143000.mp4").touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
        )

        # Clip within segment: 14:35:00 - 14:36:00
        segments = tee_manager.find_segments_for_timerange(
            start_time=datetime(2023, 12, 17, 14, 35, 0),
            end_time=datetime(2023, 12, 17, 14, 36, 0),
        )

        assert len(segments) == 1
        assert "143000" in segments[0].name

    def test_find_segments_for_timerange_multiple_segments(self, tmp_path):
        """Find multiple segments spanning clip."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create segments: 14:30-14:40, 14:40-14:50, 14:50-15:00
        (buffer_dir / "segment_20231217_143000.mp4").touch()
        (buffer_dir / "segment_20231217_144000.mp4").touch()
        (buffer_dir / "segment_20231217_145000.mp4").touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
        )

        # Clip spanning boundaries: 14:38:00 - 14:42:00
        segments = tee_manager.find_segments_for_timerange(
            start_time=datetime(2023, 12, 17, 14, 38, 0),
            end_time=datetime(2023, 12, 17, 14, 42, 0),
        )

        assert len(segments) == 2
        assert "143000" in segments[0].name
        assert "144000" in segments[1].name

    def test_find_segments_for_timerange_boundary_exact(self, tmp_path):
        """Clip starting exactly at segment boundary."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create segments: 14:30-14:40, 14:40-14:50
        (buffer_dir / "segment_20231217_143000.mp4").touch()
        (buffer_dir / "segment_20231217_144000.mp4").touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
        )

        # Clip starting exactly at 14:40:00
        segments = tee_manager.find_segments_for_timerange(
            start_time=datetime(2023, 12, 17, 14, 40, 0),
            end_time=datetime(2023, 12, 17, 14, 41, 0),
        )

        assert len(segments) == 1
        assert "144000" in segments[0].name

    def test_find_segments_for_timerange_no_overlap(self, tmp_path):
        """No segments found for time range."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create segment: 14:30-14:40
        (buffer_dir / "segment_20231217_143000.mp4").touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
        )

        # Clip outside segment: 15:00-15:01
        segments = tee_manager.find_segments_for_timerange(
            start_time=datetime(2023, 12, 17, 15, 0, 0),
            end_time=datetime(2023, 12, 17, 15, 1, 0),
        )

        assert len(segments) == 0

    def test_find_segments_chronological_order(self, tmp_path):
        """Segments returned in chronological order."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create segments out of order
        (buffer_dir / "segment_20231217_145000.mp4").touch()
        (buffer_dir / "segment_20231217_143000.mp4").touch()
        (buffer_dir / "segment_20231217_144000.mp4").touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
        )

        # Get all segments
        segments = tee_manager.find_segments_for_timerange(
            start_time=datetime(2023, 12, 17, 14, 30, 0),
            end_time=datetime(2023, 12, 17, 15, 0, 0),
        )

        assert len(segments) == 3
        assert "143000" in segments[0].name
        assert "144000" in segments[1].name
        assert "145000" in segments[2].name

    @patch("subprocess.run")
    def test_extract_clip_single_segment(self, mock_run, tmp_path):
        """Extract clip from single segment."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create segment: 14:30:00 - 14:40:00
        segment_file = buffer_dir / "segment_20231217_143000.mp4"
        segment_file.touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
            encoder="libx264",
        )

        # Mock successful extraction
        mock_run.return_value = Mock(returncode=0, stderr="")

        output_path = tmp_path / "clip.mp4"

        # Extract clip: 14:32:00 - 14:33:00 (30 seconds offset into segment)
        result = tee_manager.extract_clip(
            start_time=datetime(2023, 12, 17, 14, 32, 0),
            duration_seconds=60,
            output_path=output_path,
        )

        assert result is True
        mock_run.assert_called_once()

        # Verify ffmpeg command
        cmd = mock_run.call_args[0][0]
        assert "ffmpeg" in cmd
        assert "-ss" in cmd
        ss_index = cmd.index("-ss")
        assert cmd[ss_index + 1] == "120.0"  # 2 minutes offset
        assert "-t" in cmd
        t_index = cmd.index("-t")
        assert cmd[t_index + 1] == "60"

    @patch("subprocess.run")
    def test_extract_clip_multi_segment(self, mock_run, tmp_path):
        """Extract clip spanning multiple segments."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create segments: 14:30-14:40, 14:40-14:50
        (buffer_dir / "segment_20231217_143000.mp4").touch()
        (buffer_dir / "segment_20231217_144000.mp4").touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
            encoder="libx264",
        )

        # Mock successful extraction
        mock_run.return_value = Mock(returncode=0, stderr="")

        output_path = tmp_path / "clip.mp4"

        # Extract clip: 14:38:00 - 14:42:00 (spans two segments)
        result = tee_manager.extract_clip(
            start_time=datetime(2023, 12, 17, 14, 38, 0),
            duration_seconds=240,  # 4 minutes
            output_path=output_path,
        )

        assert result is True
        mock_run.assert_called_once()

        # Verify concat demuxer used
        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        assert "concat" in cmd
        assert "-ss" in cmd
        ss_index = cmd.index("-ss")
        assert cmd[ss_index + 1] == "480.0"  # 8 minutes offset from first segment

    @patch("subprocess.run")
    def test_extract_clip_ffmpeg_failure(self, mock_run, tmp_path):
        """Handle ffmpeg extraction failure."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        # Create segment
        (buffer_dir / "segment_20231217_143000.mp4").touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
        )

        # Mock failed extraction
        mock_run.return_value = Mock(returncode=1, stderr="Error: file not found")

        output_path = tmp_path / "clip.mp4"

        result = tee_manager.extract_clip(
            start_time=datetime(2023, 12, 17, 14, 32, 0),
            duration_seconds=60,
            output_path=output_path,
        )

        assert result is False

    def test_extract_clip_no_segments(self, tmp_path):
        """Extraction fails when no segments found."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
        )

        output_path = tmp_path / "clip.mp4"

        result = tee_manager.extract_clip(
            start_time=datetime(2023, 12, 17, 14, 32, 0),
            duration_seconds=60,
            output_path=output_path,
        )

        assert result is False

    @patch("subprocess.run")
    def test_extract_clip_custom_parameters(self, mock_run, tmp_path):
        """Extract clip with custom fps and crf."""
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()

        segment_file = buffer_dir / "segment_20231217_143000.mp4"
        segment_file.touch()

        tee_manager = FFmpegTeeManager(
            stream_url="test",
            proxy_url="test",
            buffer_dir=buffer_dir,
            chunk_minutes=10,
            encoder="libx264",
        )

        mock_run.return_value = Mock(returncode=0, stderr="")

        output_path = tmp_path / "clip.mp4"

        result = tee_manager.extract_clip(
            start_time=datetime(2023, 12, 17, 14, 32, 0),
            duration_seconds=60,
            output_path=output_path,
            fps=15,
            crf=28,
        )

        assert result is True

        # Verify custom parameters in command
        cmd = mock_run.call_args[0][0]
        assert "-r" in cmd
        r_index = cmd.index("-r")
        assert cmd[r_index + 1] == "15"
        assert "-crf" in cmd
        crf_index = cmd.index("-crf")
        assert cmd[crf_index + 1] == "28"
