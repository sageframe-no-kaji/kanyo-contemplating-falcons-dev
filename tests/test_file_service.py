"""
Tests for file service cleanup functionality.
"""

import tempfile
from pathlib import Path

import sys

# Import from admin web app
sys.path.insert(0, str(Path(__file__).parent.parent / "admin" / "web"))

from app.services import file_service  # noqa: E402


class TestCleanupTempFiles:
    """Test temporary file cleanup functionality."""

    def test_cleanup_deletes_tmp_files(self):
        """Should delete .tmp files and return count."""
        # Create temp directory structure
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            stream_dir = data_dir / "test-stream" / "clips" / "2026-01-02"
            stream_dir.mkdir(parents=True, exist_ok=True)

            # Create test files
            tmp_file1 = stream_dir / "visit_123.mp4.tmp"
            tmp_file2 = stream_dir / "arrival_456.mp4.tmp"
            mp4_file = stream_dir / "complete_789.mp4"

            tmp_file1.write_bytes(b"x" * 1000)  # 1KB
            tmp_file2.write_bytes(b"x" * 2000)  # 2KB
            mp4_file.write_bytes(b"x" * 5000)  # 5KB (should not be deleted)

            # Run cleanup
            result = file_service.cleanup_temp_files("test-stream", str(data_dir))

            # Verify results
            assert result["files_deleted"] == 2
            assert result["bytes_freed"] == 3000
            assert not tmp_file1.exists()
            assert not tmp_file2.exists()
            assert mp4_file.exists()  # Should still exist

    def test_cleanup_deletes_ffmpeg_log_files(self):
        """Should delete .ffmpeg.log files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            stream_dir = data_dir / "test-stream" / "clips" / "2026-01-02"
            stream_dir.mkdir(parents=True, exist_ok=True)

            # Create test files
            log_file1 = stream_dir / "visit_123.mp4.ffmpeg.log"
            log_file2 = stream_dir / "arrival_456.mp4.ffmpeg.log"
            mp4_file = stream_dir / "complete_789.mp4"

            log_file1.write_bytes(b"x" * 500)
            log_file2.write_bytes(b"x" * 700)
            mp4_file.write_bytes(b"x" * 5000)

            # Run cleanup
            result = file_service.cleanup_temp_files("test-stream", str(data_dir))

            # Verify results
            assert result["files_deleted"] == 2
            assert result["bytes_freed"] == 1200
            assert not log_file1.exists()
            assert not log_file2.exists()
            assert mp4_file.exists()

    def test_cleanup_handles_nested_directories(self):
        """Should find and delete temp files in nested directories."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            stream_dir = data_dir / "test-stream" / "clips"
            stream_dir.mkdir(parents=True, exist_ok=True)

            # Create nested structure
            day1 = stream_dir / "2026-01-01"
            day2 = stream_dir / "2026-01-02"
            day1.mkdir()
            day2.mkdir()

            # Create temp files in different directories
            tmp1 = day1 / "visit_1.mp4.tmp"
            tmp2 = day2 / "visit_2.mp4.tmp"
            log1 = day1 / "arrival_1.mp4.ffmpeg.log"

            tmp1.write_bytes(b"x" * 1000)
            tmp2.write_bytes(b"x" * 2000)
            log1.write_bytes(b"x" * 500)

            # Run cleanup
            result = file_service.cleanup_temp_files("test-stream", str(data_dir))

            # Verify all temp files deleted
            assert result["files_deleted"] == 3
            assert result["bytes_freed"] == 3500
            assert not tmp1.exists()
            assert not tmp2.exists()
            assert not log1.exists()

    def test_cleanup_empty_directory(self):
        """Should handle empty clips directory gracefully."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)

            # Run cleanup on non-existent stream
            result = file_service.cleanup_temp_files("nonexistent", str(data_dir))

            # Should return zero results
            assert result["files_deleted"] == 0
            assert result["bytes_freed"] == 0
            assert result["deleted_files"] == []

    def test_cleanup_preserves_complete_files(self):
        """Should not delete complete .mp4 or other files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            stream_dir = data_dir / "test-stream" / "clips" / "2026-01-02"
            stream_dir.mkdir(parents=True, exist_ok=True)

            # Create various file types
            mp4_file = stream_dir / "visit_123.mp4"
            jpg_file = stream_dir / "photo_123.jpg"
            txt_file = stream_dir / "notes.txt"
            tmp_file = stream_dir / "incomplete.mp4.tmp"

            mp4_file.write_bytes(b"x" * 5000)
            jpg_file.write_bytes(b"x" * 3000)
            txt_file.write_bytes(b"x" * 100)
            tmp_file.write_bytes(b"x" * 2000)

            # Run cleanup
            result = file_service.cleanup_temp_files("test-stream", str(data_dir))

            # Only tmp file should be deleted
            assert result["files_deleted"] == 1
            assert mp4_file.exists()
            assert jpg_file.exists()
            assert txt_file.exists()
            assert not tmp_file.exists()
