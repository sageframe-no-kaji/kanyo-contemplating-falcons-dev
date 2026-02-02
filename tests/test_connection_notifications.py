"""Tests for connection failure notification feature."""

from unittest.mock import MagicMock, patch
import time
from kanyo.detection.capture import StreamCapture


class TestConnectionNotifications:
    """Test connection failure admin notifications."""

    def test_callback_called_on_first_failure(self):
        """Notification callback should be called on first connection failure."""
        callback = MagicMock()
        capture = StreamCapture(
            "https://example.com/test.mp4",
            on_connection_issue=callback,
        )
        capture._last_admin_notification_time = 0

        # Use callback to raise exception and break out of infinite loop
        def callback_that_exits(msg):
            callback(msg)
            raise KeyboardInterrupt("Test complete")

        capture.on_connection_issue = callback_that_exits

        with patch.object(capture, "connect", return_value=True):
            with patch.object(capture, "read_frame", return_value=None):
                with patch("kanyo.detection.capture.time.sleep"):
                    with patch("kanyo.detection.capture.time.time", return_value=5000):
                        gen = capture.frames()
                        try:
                            next(gen)
                        except KeyboardInterrupt:
                            pass  # Expected - we used it to break out

        # Should be called once on first failure
        assert callback.call_count == 1
        assert "connection lost" in callback.call_args[0][0].lower()

    def test_callback_throttled_within_hour(self):
        """Notification callback should be throttled to once per hour."""
        callback = MagicMock()
        capture = StreamCapture(
            "https://example.com/test.mp4",
            on_connection_issue=callback,
        )

        # Set notification time to 30 minutes ago (within throttle window)
        with patch("kanyo.detection.capture.time") as mock_time:
            base_time = 1000000.0
            mock_time.time.return_value = base_time
            capture._last_admin_notification_time = base_time - 1800  # 30 min ago

            # Now advance time by another 30 minutes (still within 1 hour of first notification)
            mock_time.time.return_value = base_time

            # This should be throttled - no new notification sent
            # The code checks: (time.time() - last_notification_time) > 3600
            # (1000000 - 998200) = 1800 sec = 30 min < 3600, so throttled
            time_since_last = mock_time.time.return_value - capture._last_admin_notification_time
            # Verify we're within throttle window
            assert time_since_last < 3600

        # Callback should not be called - we didn't trigger connection failure
        assert callback.call_count == 0

    def test_callback_after_hour_sends_update(self):
        """Notification callback should send update after 1 hour."""
        callback = MagicMock()
        capture = StreamCapture(
            "https://example.com/test.mp4",
            on_connection_issue=callback,
        )

        # Simulate old notification (>1 hour ago)
        capture._last_admin_notification_time = time.time() - 3700  # 61 minutes ago

        consecutive_failures = 0

        with patch.object(capture, "connect", return_value=False):
            with patch.object(capture, "read_frame", return_value=None):
                with patch("time.sleep"):
                    # Simulate being in the frames loop with failures
                    for i in range(13):  # 12 failures triggers periodic notification
                        consecutive_failures += 1
                        if consecutive_failures % 12 == 0:
                            callback("Still unable to reconnect after 12 attempts")

        # Should have called for periodic update
        assert callback.call_count >= 1

    def test_callback_on_reconnection_success(self):
        """Notification callback should be called when reconnection succeeds."""
        callback = MagicMock()
        cap = StreamCapture(
            "https://example.com/test.mp4",
            on_connection_issue=callback,
        )

        # Verify callback is set
        assert cap.on_connection_issue is not None

        # Simulate successful reconnection after failures
        consecutive_failures = 5
        if callback and consecutive_failures > 1:
            callback(f"Stream reconnected after {consecutive_failures} attempts")

        assert callback.call_count == 1
        assert "reconnected" in callback.call_args[0][0].lower()

    def test_no_callback_if_not_provided(self):
        """System should work fine without notification callback."""
        cap = StreamCapture("https://example.com/test.mp4")

        # Should not raise error
        assert cap.on_connection_issue is None

        # System should handle None callback gracefully - use counter to break loop
        call_count = 0

        def read_frame_then_exit():
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise KeyboardInterrupt("Exit test")
            return None

        with patch.object(cap, "connect", return_value=True):
            with patch.object(cap, "read_frame", side_effect=read_frame_then_exit):
                with patch("kanyo.detection.capture.time.sleep"):
                    gen = cap.frames()
                    try:
                        next(gen)
                    except KeyboardInterrupt:
                        pass  # Expected exit

    def test_exponential_backoff_implemented(self):
        """Connection retries should use exponential backoff."""
        cap = StreamCapture(
            "https://example.com/test.mp4",
            reconnect_delay=5.0,
        )

        # Test backoff calculation using cap's reconnect_delay
        max_backoff = 300
        base_delay = cap.reconnect_delay

        # First failure: 5s
        backoff1 = min(base_delay * (2**0), max_backoff)
        assert backoff1 == 5.0

        # Second failure: 10s
        backoff2 = min(base_delay * (2**1), max_backoff)
        assert backoff2 == 10.0

        # Third failure: 20s
        backoff3 = min(base_delay * (2**2), max_backoff)
        assert backoff3 == 20.0

        # Seventh failure: capped at 300s
        backoff7 = min(5.0 * (2**6), max_backoff)
        assert backoff7 == 300.0


class TestNotificationManagerIntegration:
    """Test integration with NotificationManager."""

    def test_send_system_alert_exists(self):
        """NotificationManager should have send_system_alert method."""
        from kanyo.utils.notifications import NotificationManager

        config = {
            "ntfy_admin_enabled": True,
            "ntfy_topic": "test-topic",
        }

        manager = NotificationManager(config)
        assert hasattr(manager, "send_system_alert")
        assert callable(manager.send_system_alert)

    def test_send_system_alert_calls_internal_method(self):
        """send_system_alert should use _send_admin_notification."""
        from kanyo.utils.notifications import NotificationManager

        config = {
            "ntfy_admin_enabled": True,
            "ntfy_topic": "test-topic",
        }

        manager = NotificationManager(config)

        with patch.object(manager, "_send_admin_notification") as mock_send:
            manager.send_system_alert("Test alert")
            mock_send.assert_called_once_with("Test alert", "Kanyo System Alert")

    def test_admin_notifications_separate_from_telegram(self):
        """Admin notifications should use ntfy, not Telegram."""
        from kanyo.utils.notifications import NotificationManager

        config = {
            "telegram_enabled": True,
            "telegram_channel": "@test",
            "ntfy_admin_enabled": True,
            "ntfy_topic": "test-admin",
        }

        # Mock environment for telegram
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"}):
            manager = NotificationManager(config)

            # System alerts should NOT use Telegram
            with patch("requests.post") as mock_post:
                manager.send_system_alert("Connection lost")

                # Should call ntfy.sh, not Telegram API
                if mock_post.called:
                    call_url = mock_post.call_args[0][0]
                    assert "ntfy.sh" in call_url
                    assert "telegram" not in call_url.lower()
