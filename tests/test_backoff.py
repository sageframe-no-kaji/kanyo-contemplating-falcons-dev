"""Tests for exponential backoff in stream reconnection."""

from unittest.mock import MagicMock, patch

from kanyo.detection.capture import (
    BACKOFF_MAX_SECONDS,
    BACKOFF_MIN_SECONDS,
    MAX_DAILY_ATTEMPTS,
    StreamCapture,
)


class TestExponentialBackoff:
    """Test exponential backoff behavior in connect()."""

    @patch("kanyo.detection.capture.subprocess.run")
    @patch("kanyo.detection.capture.cv2.VideoCapture")
    @patch("kanyo.detection.capture.time.sleep")
    def test_consecutive_failures_increase_backoff(self, mock_sleep, mock_cv2, mock_subprocess):
        """Consecutive failures should double the backoff delay."""
        # yt-dlp succeeds but VideoCapture fails to open
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://example.com/stream.m3u8\n"
        mock_subprocess.return_value = mock_result

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cv2.return_value = mock_cap

        capture = StreamCapture("https://www.youtube.com/watch?v=test")

        # Seed random for reproducible jitter
        with patch("kanyo.detection.capture.random.random", return_value=0.5):
            capture.connect()  # failure 1
            delay_1 = mock_sleep.call_args[0][0]

            capture.connect()  # failure 2
            delay_2 = mock_sleep.call_args[0][0]

            capture.connect()  # failure 3
            delay_3 = mock_sleep.call_args[0][0]

        # Each delay should be roughly double the previous (with fixed jitter=0)
        assert delay_2 > delay_1
        assert delay_3 > delay_2
        assert capture._consecutive_failures == 3

    @patch("kanyo.detection.capture.subprocess.run")
    @patch("kanyo.detection.capture.cv2.VideoCapture")
    @patch("kanyo.detection.capture.time.sleep")
    def test_success_resets_consecutive_failures(self, mock_sleep, mock_cv2, mock_subprocess):
        """Successful connection should reset _consecutive_failures to 0."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://example.com/stream.m3u8\n"
        mock_subprocess.return_value = mock_result

        # First call: fail. Second call: succeed.
        mock_cap_fail = MagicMock()
        mock_cap_fail.isOpened.return_value = False
        mock_cap_success = MagicMock()
        mock_cap_success.isOpened.return_value = True
        mock_cv2.side_effect = [mock_cap_fail, mock_cap_success]

        capture = StreamCapture("https://www.youtube.com/watch?v=test")
        capture.connect()  # fails
        assert capture._consecutive_failures == 1

        capture.connect()  # succeeds
        assert capture._consecutive_failures == 0

    @patch("kanyo.detection.capture.time.sleep")
    @patch("kanyo.detection.capture.time.time")
    def test_daily_cap_triggers_dormancy(self, mock_time, mock_sleep):
        """Exceeding daily attempt cap should trigger 1-hour dormancy."""
        mock_time.return_value = 1000000.0

        capture = StreamCapture("https://www.youtube.com/watch?v=test")
        capture._attempts_today = MAX_DAILY_ATTEMPTS
        capture._attempts_window_start = 999000.0  # within 24h window

        result = capture.connect()

        assert result is False
        mock_sleep.assert_called_with(3600)

    @patch("kanyo.detection.capture.subprocess.run")
    @patch("kanyo.detection.capture.cv2.VideoCapture")
    @patch("kanyo.detection.capture.time.sleep")
    def test_backoff_cap_respected(self, mock_sleep, mock_cv2, mock_subprocess):
        """10 consecutive failures should not exceed BACKOFF_MAX_SECONDS + jitter."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://example.com/stream.m3u8\n"
        mock_subprocess.return_value = mock_result

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cv2.return_value = mock_cap

        capture = StreamCapture("https://www.youtube.com/watch?v=test")

        for _ in range(10):
            capture.connect()

        assert capture._consecutive_failures == 10

        # Every sleep call should be within bounds
        max_with_jitter = BACKOFF_MAX_SECONDS * 1.2  # max positive jitter
        for call in mock_sleep.call_args_list:
            delay = call[0][0]
            assert delay >= BACKOFF_MIN_SECONDS * 0.8  # min minus max negative jitter
            assert delay <= max_with_jitter

    @patch("kanyo.detection.capture.time.sleep")
    @patch("kanyo.detection.capture.time.time")
    def test_daily_window_resets_after_24h(self, mock_time, mock_sleep):
        """Daily attempt counter should reset after 24 hours."""
        capture = StreamCapture("https://www.youtube.com/watch?v=test")
        capture._attempts_today = MAX_DAILY_ATTEMPTS
        capture._attempts_window_start = 1000000.0

        # Time is now 24h + 1s later
        mock_time.return_value = 1000000.0 + 86401

        # _check_daily_cap should reset and return True
        assert capture._check_daily_cap() is True
        assert capture._attempts_today == 0
