"""Tests for live_tee module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

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
