"""
Tests for YouTube stream recovery with fallback client.
"""

from unittest.mock import MagicMock, patch

from kanyo.detection.capture import StreamCapture


class TestYouTubeRecovery:
    """Test YouTube stream recovery mechanisms."""

    @patch("kanyo.detection.capture.subprocess.run")
    @patch("kanyo.detection.capture.cv2.VideoCapture")
    def test_successful_connection_resets_fallback_flag(self, mock_cv2, mock_subprocess):
        """Test that successful connection resets the fallback flag."""
        # Setup successful yt-dlp response
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://example.com/stream.m3u8\n"
        mock_subprocess.return_value = mock_result

        # Setup successful video capture
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cv2.return_value = mock_cap

        capture = StreamCapture("https://www.youtube.com/watch?v=test")
        capture._ytdlp_fallback_used = True  # Simulate previous fallback

        result = capture.connect()

        assert result is True
        assert capture._ytdlp_fallback_used is False

    @patch("kanyo.detection.capture.subprocess.run")
    @patch("kanyo.detection.capture.cv2.VideoCapture")
    def test_precondition_failed_triggers_fallback(self, mock_cv2, mock_subprocess):
        """Test that 'Precondition check failed' triggers fallback client."""
        # First call fails with precondition error
        mock_result_fail = MagicMock()
        mock_result_fail.returncode = 1
        mock_result_fail.stderr = "ERROR: Precondition check failed"

        # Second call succeeds with fallback
        mock_result_success = MagicMock()
        mock_result_success.returncode = 0
        mock_result_success.stdout = "https://example.com/stream.m3u8\n"

        mock_subprocess.side_effect = [mock_result_fail, mock_result_success]

        # Setup successful video capture
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cv2.return_value = mock_cap

        capture = StreamCapture("https://www.youtube.com/watch?v=test")

        result = capture.connect()

        assert result is True
        # Fallback was triggered (ytdlp_opts modified)
        assert "extractor_args" in capture.ytdlp_opts
        # Flag is reset after successful connection
        assert capture._ytdlp_fallback_used is False
        # Should have made two subprocess calls (initial + fallback)
        assert mock_subprocess.call_count == 2

    @patch("kanyo.detection.capture.subprocess.run")
    @patch("kanyo.detection.capture.time.sleep")
    def test_fallback_failure_triggers_cooldown(self, mock_sleep, mock_subprocess):
        """Test that fallback failure triggers 5-minute cooldown."""
        # Both calls fail with precondition error
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ERROR: Precondition check failed"
        mock_subprocess.return_value = mock_result

        capture = StreamCapture("https://www.youtube.com/watch?v=test")

        result = capture.connect()

        assert result is False
        # Should have called sleep with 300 seconds (5 minutes)
        mock_sleep.assert_called_with(300)

    def test_initial_state(self):
        """Test that StreamCapture initializes with correct default state."""
        capture = StreamCapture("https://www.youtube.com/watch?v=test")

        assert capture._ytdlp_fallback_used is False
        assert capture.ytdlp_opts == {}

    @patch("kanyo.detection.capture.subprocess.run")
    @patch("kanyo.detection.capture.cv2.VideoCapture")
    def test_non_youtube_url_unaffected(self, mock_cv2, mock_subprocess):
        """Test that non-YouTube URLs bypass the fallback logic."""
        # Setup successful video capture
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cv2.return_value = mock_cap

        capture = StreamCapture("http://example.com/stream.mp4")

        result = capture.connect()

        assert result is True
        # Should not call yt-dlp at all
        mock_subprocess.assert_not_called()
        # Should not modify fallback state
        assert capture._ytdlp_fallback_used is False
        assert capture.ytdlp_opts == {}

    @patch("kanyo.detection.capture.subprocess.run")
    def test_extractor_args_passed_to_ytdlp(self, mock_subprocess):
        """Test that extractor args are passed to yt-dlp when in fallback mode."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://example.com/stream.m3u8\n"
        mock_subprocess.return_value = mock_result

        capture = StreamCapture("https://www.youtube.com/watch?v=test")
        capture._ytdlp_fallback_used = True
        capture.ytdlp_opts["extractor_args"] = {"youtube": {"player_client": ["android_creator"]}}

        capture.resolve_youtube_url()

        # Check that the command includes extractor args
        call_args = mock_subprocess.call_args[0][0]
        assert "--extractor-args" in call_args
        assert "youtube:player_client=android_creator" in call_args
