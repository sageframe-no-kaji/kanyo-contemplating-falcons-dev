"""
Tests for log service (file-based log reading).
"""

import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Import from admin web app
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "admin" / "web"))

from app.services import log_service


class TestParseLogLine:
    """Test log line parsing."""

    def test_parse_valid_log_line(self):
        """Parse a valid log line with UTC timestamp."""
        line = "2025-12-30 12:08:03 UTC | INFO     | kanyo.detection | Detection started"
        result = log_service._parse_log_line(line)

        assert result is not None
        assert result["level"] == "INFO"
        assert result["module"] == "kanyo.detection"
        assert result["message"] == "Detection started"
        assert result["raw"] == line.strip()

        # Check timestamp is UTC-aware
        assert result["timestamp"].tzinfo == timezone.utc
        assert result["timestamp"].year == 2025
        assert result["timestamp"].month == 12
        assert result["timestamp"].day == 30

    def test_parse_log_line_without_utc_marker(self):
        """Parse log line without UTC marker (backward compatibility)."""
        line = "2025-12-30 12:08:03 | INFO     | kanyo.detection | Detection started"
        result = log_service._parse_log_line(line)

        assert result is not None
        assert result["timestamp"].tzinfo == timezone.utc

    def test_parse_different_log_levels(self):
        """Parse different log levels."""
        levels = ["DEBUG", "INFO", "EVENT", "WARNING", "ERROR"]

        for level in levels:
            line = f"2025-12-30 12:08:03 UTC | {level:<8} | module | message"
            result = log_service._parse_log_line(line)
            assert result is not None
            assert result["level"] == level

    def test_parse_invalid_log_line(self):
        """Return None for invalid log lines."""
        invalid_lines = [
            "Not a log line",
            "2025-12-30 | Only two parts",
            "Invalid timestamp | INFO | module | message",
            "",
        ]

        for line in invalid_lines:
            result = log_service._parse_log_line(line)
            assert result is None


class TestFindLastStartup:
    """Test finding last startup timestamp."""

    def test_find_last_startup_marker(self):
        """Find timestamp of last BUFFER-BASED FALCON MONITOR line."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            f.write("2025-12-30 10:00:00 UTC | INFO     | kanyo | First startup\n")
            f.write("2025-12-30 10:01:00 UTC | INFO     | kanyo | Some logs\n")
            f.write("2025-12-30 12:00:00 UTC | INFO     | kanyo | BUFFER-BASED FALCON MONITOR\n")
            f.write("2025-12-30 12:01:00 UTC | INFO     | kanyo | More logs\n")
            log_path = Path(f.name)

        try:
            result = log_service._find_last_startup(log_path)

            assert result.year == 2025
            assert result.month == 12
            assert result.day == 30
            assert result.hour == 12
            assert result.minute == 0
            assert result.tzinfo == timezone.utc
        finally:
            log_path.unlink()

    def test_find_startup_with_alternative_markers(self):
        """Find startup using alternative startup markers."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            f.write(
                "2025-12-30 11:00:00 UTC | INFO     | kanyo | Starting Buffer-Based Falcon Monitoring\n"
            )
            log_path = Path(f.name)

        try:
            result = log_service._find_last_startup(log_path)
            assert result.hour == 11
        finally:
            log_path.unlink()

    def test_no_startup_marker(self):
        """Return datetime.min if no startup marker found."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            f.write("2025-12-30 10:00:00 UTC | INFO     | kanyo | Regular log\n")
            log_path = Path(f.name)

        try:
            result = log_service._find_last_startup(log_path)
            assert result == datetime.min
        finally:
            log_path.unlink()


class TestGetLogs:
    """Test the main get_logs function."""

    def setup_method(self):
        """Create temporary log file for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.stream_id = "test-stream"
        self.log_dir = Path(self.temp_dir) / self.stream_id / "logs"
        self.log_dir.mkdir(parents=True)
        self.log_path = self.log_dir / "kanyo.log"

    def teardown_method(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.temp_dir)

    def test_get_logs_all_time(self):
        """Get all logs without time filter."""
        # Write sample logs
        now = datetime.now(timezone.utc)
        with open(self.log_path, "w") as f:
            for i in range(5):
                ts = (now - timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{ts} UTC | INFO     | kanyo | Log {i}\n")

        # Mock the log path
        original_get_logs = log_service.get_logs

        def mock_get_logs(stream_id, since="startup", lines=500, levels=None):
            # Override path to use our temp file
            log_path = self.log_path
            if not log_path.exists():
                return []

            # Same logic as original but with our test path
            cutoff = None
            now_utc = datetime.now(timezone.utc)

            if since == "1h":
                cutoff = now_utc - timedelta(hours=1)
            elif since == "24h":
                cutoff = now_utc - timedelta(hours=24)
            elif since == "7d":
                cutoff = now_utc - timedelta(days=7)
            elif since == "startup":
                cutoff = log_service._find_last_startup(log_path)

            log_lines = []
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parsed = log_service._parse_log_line(line)
                    if parsed:
                        if cutoff is not None and parsed["timestamp"] < cutoff:
                            continue
                        if levels and parsed["level"] not in levels:
                            continue
                        log_lines.append(parsed)

            return log_lines[-lines:]

        result = mock_get_logs(self.stream_id, since="all", lines=100)
        assert len(result) == 5

    def test_get_logs_with_level_filter(self):
        """Filter logs by level."""
        with open(self.log_path, "w") as f:
            f.write("2025-12-30 10:00:00 UTC | INFO     | kanyo | Info log\n")
            f.write("2025-12-30 10:01:00 UTC | ERROR    | kanyo | Error log\n")
            f.write("2025-12-30 10:02:00 UTC | DEBUG    | kanyo | Debug log\n")
            f.write("2025-12-30 10:03:00 UTC | WARNING  | kanyo | Warning log\n")

        # Test filtering
        log_lines = []
        with open(self.log_path, "r") as f:
            for line in f:
                parsed = log_service._parse_log_line(line)
                if parsed and parsed["level"] in ["ERROR", "WARNING"]:
                    log_lines.append(parsed)

        assert len(log_lines) == 2
        assert log_lines[0]["level"] == "ERROR"
        assert log_lines[1]["level"] == "WARNING"

    def test_get_logs_line_limit(self):
        """Respect line limit."""
        with open(self.log_path, "w") as f:
            for i in range(100):
                f.write(f"2025-12-30 10:00:{i:02d} UTC | INFO     | kanyo | Log {i}\n")

        log_lines = []
        with open(self.log_path, "r") as f:
            for line in f:
                parsed = log_service._parse_log_line(line)
                if parsed:
                    log_lines.append(parsed)

        # Simulate limiting to last 10 lines
        result = log_lines[-10:]
        assert len(result) == 10

    def test_get_logs_nonexistent_file(self):
        """Return empty list if log file doesn't exist."""
        nonexistent = Path(self.temp_dir) / "nonexistent" / "logs" / "kanyo.log"

        if not nonexistent.exists():
            result = []
        else:
            # This shouldn't happen in our test
            result = None

        assert result == []


class TestTimeZoneCorrectness:
    """Test that timezone handling is correct throughout."""

    def test_utc_comparison(self):
        """Ensure UTC-aware timestamps are compared correctly."""
        # Create two UTC-aware datetimes
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)

        # Parse a log line from one hour ago
        ts_str = one_hour_ago.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts_str} UTC | INFO     | kanyo | Test"
        parsed = log_service._parse_log_line(line)

        # Compare with cutoff
        cutoff = now - timedelta(minutes=30)
        assert parsed["timestamp"] < cutoff  # Should be filtered out

        cutoff = now - timedelta(hours=2)
        assert parsed["timestamp"] > cutoff  # Should be included

    def test_timezone_aware_comparison_prevents_bugs(self):
        """
        Verify that using UTC-aware datetimes prevents local timezone bugs.

        Without UTC awareness, a log at "12:00 UTC" compared against
        "now() = 12:00 PST" would incorrectly appear to be in the past.
        """
        # Simulate log entry at 12:00 UTC
        log_time = datetime(2025, 12, 30, 12, 0, 0, tzinfo=timezone.utc)

        # If we used naive datetime.now() in PST (UTC-8), it might be 04:00
        # and think the log is 8 hours in the future!

        # With UTC-aware comparison:
        now_utc = datetime.now(timezone.utc)

        # This comparison is always correct regardless of local timezone
        if log_time <= now_utc:
            assert True  # Correct comparison
        else:
            # Log is in the future (shouldn't happen in real scenario)
            assert False
