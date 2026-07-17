"""Tests for NotificationManager."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from kanyo.utils.notifications import NotificationManager


def _make_manager(
    telegram_enabled=False,
    ntfy_admin_enabled=False,
    cooldown=5,
    token="testtoken",
    channel="@testchan",
    ntfy_topic="testtopic",
):
    """Helper to create a NotificationManager with controllable config."""
    config = {
        "telegram_enabled": telegram_enabled,
        "ntfy_admin_enabled": ntfy_admin_enabled,
        "notification_cooldown_minutes": cooldown,
        "telegram_channel": channel,
        "ntfy_topic": ntfy_topic,
    }
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token}):
        return NotificationManager(config)


class TestNotificationManagerInit:
    def test_disabled_by_default(self):
        mgr = _make_manager()
        assert mgr.telegram_enabled is False
        assert mgr.ntfy_admin_enabled is False

    def test_telegram_disabled_when_no_token(self):
        """If telegram_enabled=True but no token, it should disable itself."""
        config = {"telegram_enabled": True, "telegram_channel": "@chan"}
        with patch.dict("os.environ", {}, clear=True):
            mgr = NotificationManager(config)
        assert mgr.telegram_enabled is False

    def test_ntfy_disabled_when_no_topic(self):
        """If ntfy_admin_enabled=True but no topic, it should disable itself."""
        config = {"ntfy_admin_enabled": True}
        mgr = NotificationManager(config)
        assert mgr.ntfy_admin_enabled is False

    def test_valid_telegram_config(self):
        mgr = _make_manager(telegram_enabled=True)
        assert mgr.telegram_enabled is True
        assert mgr.cooldown_minutes == 5

    def test_valid_ntfy_config(self):
        mgr = _make_manager(ntfy_admin_enabled=True)
        assert mgr.ntfy_admin_enabled is True


class TestSendArrival:
    def test_returns_false_when_disabled(self):
        mgr = _make_manager()
        assert mgr.send_arrival(datetime.now(), None) is False

    def test_suppressed_during_cooldown(self):
        mgr = _make_manager(telegram_enabled=True, cooldown=5)
        now = datetime.now()
        mgr.last_departure_time = now - timedelta(minutes=2)  # 2m < 5m cooldown
        result = mgr.send_arrival(now, None)
        assert result is False

    def test_allowed_after_cooldown(self, tmp_path):
        mgr = _make_manager(telegram_enabled=True, cooldown=5)
        now = datetime.now()
        mgr.last_departure_time = now - timedelta(minutes=10)  # 10m > 5m cooldown

        thumb = tmp_path / "thumb.jpg"
        thumb.write_bytes(b"fake jpeg")

        mock_resp = Mock(status_code=200)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp):
            result = mgr.send_arrival(now, thumb)

        assert result is True

    def test_no_cooldown_on_first_arrival(self, tmp_path):
        """First arrival has no last_departure_time, so no cooldown."""
        mgr = _make_manager(telegram_enabled=True)
        assert mgr.last_departure_time is None

        thumb = tmp_path / "thumb.jpg"
        thumb.write_bytes(b"fake jpeg")

        mock_resp = Mock(status_code=200)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp):
            result = mgr.send_arrival(datetime.now(), thumb)

        assert result is True


class TestSendDeparture:
    def test_returns_false_when_disabled(self):
        mgr = _make_manager()
        assert mgr.send_departure(datetime.now(), None) is False

    def test_sends_and_starts_cooldown(self, tmp_path):
        """Successful departure sets last_departure_time."""
        mgr = _make_manager(telegram_enabled=True)
        ts = datetime.now()

        thumb = tmp_path / "dep.jpg"
        thumb.write_bytes(b"fake jpeg")

        mock_resp = Mock(status_code=200)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp):
            result = mgr.send_departure(ts, thumb, "5m")

        assert result is True
        assert mgr.last_departure_time == ts

    def test_departure_without_duration(self, tmp_path):
        mgr = _make_manager(telegram_enabled=True)
        thumb = tmp_path / "dep.jpg"
        thumb.write_bytes(b"fake jpeg")

        mock_resp = Mock(status_code=200)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp):
            result = mgr.send_departure(datetime.now(), thumb)

        assert result is True


class TestSendTelegramPhoto:
    def test_returns_false_when_no_photo_path(self):
        mgr = _make_manager(telegram_enabled=True)
        assert mgr._send_telegram_photo("caption", None) is False

    def test_returns_false_when_photo_not_found(self, tmp_path):
        mgr = _make_manager(telegram_enabled=True)
        result = mgr._send_telegram_photo("caption", tmp_path / "missing.jpg")
        assert result is False

    def test_api_error_response(self, tmp_path):
        mgr = _make_manager(telegram_enabled=True)
        thumb = tmp_path / "t.jpg"
        thumb.write_bytes(b"data")

        mock_resp = Mock(status_code=400, text="Bad Request")
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp):
            result = mgr._send_telegram_photo("caption", thumb)

        assert result is False

    def test_connection_error(self, tmp_path):
        import requests as req

        mgr = _make_manager(telegram_enabled=True)
        thumb = tmp_path / "t.jpg"
        thumb.write_bytes(b"data")

        with patch(
            "kanyo.utils.notifications.requests.post",
            side_effect=req.ConnectionError("refused"),
        ):
            result = mgr._send_telegram_photo("caption", thumb)

        assert result is False

    def test_timeout_error(self, tmp_path):
        import requests as req

        mgr = _make_manager(telegram_enabled=True)
        thumb = tmp_path / "t.jpg"
        thumb.write_bytes(b"data")

        with patch(
            "kanyo.utils.notifications.requests.post",
            side_effect=req.Timeout("timed out"),
        ):
            result = mgr._send_telegram_photo("caption", thumb)

        assert result is False


class TestAdminNotifications:
    def test_send_system_alert_when_disabled(self):
        """send_system_alert does nothing when ntfy disabled."""
        mgr = _make_manager()
        # Should not raise
        mgr.send_system_alert("test message")

    def test_send_admin_notification_sends_to_ntfy(self):
        mgr = _make_manager(ntfy_admin_enabled=True)
        mock_resp = Mock(status_code=200)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp) as mock_post:
            mgr._send_admin_notification("an error", "Test Title")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "testtopic" in call_kwargs[0][0]

    def test_send_admin_notification_handles_exception(self):
        mgr = _make_manager(ntfy_admin_enabled=True)
        with patch(
            "kanyo.utils.notifications.requests.post",
            side_effect=Exception("network error"),
        ):
            # Should not raise
            mgr._send_admin_notification("error msg", "Title")

    def test_send_admin_notification_non_2xx_logged_not_raised(self):
        """A non-2xx ntfy response is logged as a warning, never raised."""
        mgr = _make_manager(ntfy_admin_enabled=True)
        mock_resp = Mock(status_code=500)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp) as mock_post:
            # Should not raise
            mgr._send_admin_notification("an error", "Title")
        mock_post.assert_called_once()

    def test_send_admin_notification_includes_title_header(self):
        """The notification title travels as the ntfy Title header."""
        mgr = _make_manager(ntfy_admin_enabled=True)
        mock_resp = Mock(status_code=200)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp) as mock_post:
            mgr.send_system_alert("stream down", title="Kanyo System Alert")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Title"] == "Kanyo System Alert"


class TestSendActivitySummary:
    def test_returns_false_when_disabled(self):
        """Summaries are dropped when Telegram is disabled."""
        mgr = _make_manager()
        assert mgr.send_activity_summary("9 visits in the last hour") is False

    def test_sends_text_message_when_enabled(self):
        """An enabled manager sends the summary as a text-only message."""
        mgr = _make_manager(telegram_enabled=True)
        mock_resp = Mock(status_code=200)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp) as mock_post:
            result = mgr.send_activity_summary("9 visits in the last hour, median 25s")

        assert result is True
        url = mock_post.call_args[0][0]
        assert "sendMessage" in url
        data = mock_post.call_args.kwargs["data"]
        assert data["text"] == "9 visits in the last hour, median 25s"
        assert data["chat_id"] == "@testchan"


class TestSendTelegramText:
    def test_success_returns_true(self):
        mgr = _make_manager(telegram_enabled=True)
        mock_resp = Mock(status_code=200)
        with patch("kanyo.utils.notifications.requests.post", return_value=mock_resp) as mock_post:
            result = mgr._send_telegram_text("hello")

        assert result is True
        url = mock_post.call_args[0][0]
        assert url == "https://api.telegram.org/bottesttoken/sendMessage"

    def test_api_error_reports_to_admin(self):
        """A non-200 Telegram response fails and pings the admin channel."""
        mgr = _make_manager(telegram_enabled=True, ntfy_admin_enabled=True)
        telegram_resp = Mock(status_code=403, text="Forbidden: bot was blocked")
        ntfy_resp = Mock(status_code=200)
        with patch(
            "kanyo.utils.notifications.requests.post",
            side_effect=[telegram_resp, ntfy_resp],
        ) as mock_post:
            result = mgr._send_telegram_text("hello")

        assert result is False
        # Second post is the admin-error notification to ntfy
        assert mock_post.call_count == 2
        assert "ntfy.sh/testtopic" in mock_post.call_args_list[1][0][0]

    def test_timeout_returns_false(self):
        import requests as req

        mgr = _make_manager(telegram_enabled=True)
        with patch(
            "kanyo.utils.notifications.requests.post",
            side_effect=req.Timeout("timed out"),
        ):
            assert mgr._send_telegram_text("hello") is False

    def test_connection_error_returns_false(self):
        import requests as req

        mgr = _make_manager(telegram_enabled=True)
        with patch(
            "kanyo.utils.notifications.requests.post",
            side_effect=req.ConnectionError("refused"),
        ):
            assert mgr._send_telegram_text("hello") is False

    def test_unexpected_error_returns_false(self):
        mgr = _make_manager(telegram_enabled=True)
        with patch(
            "kanyo.utils.notifications.requests.post",
            side_effect=ValueError("boom"),
        ):
            assert mgr._send_telegram_text("hello") is False


class TestSendTelegramPhotoUnexpectedError:
    def test_unexpected_error_returns_false(self, tmp_path):
        """A non-requests exception during photo send is caught and reported."""
        mgr = _make_manager(telegram_enabled=True)
        thumb = tmp_path / "t.jpg"
        thumb.write_bytes(b"data")

        with patch(
            "kanyo.utils.notifications.requests.post",
            side_effect=ValueError("boom"),
        ):
            assert mgr._send_telegram_photo("caption", thumb) is False
