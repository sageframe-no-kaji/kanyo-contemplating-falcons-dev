"""Tests for FalconEventHandler."""
from datetime import datetime
from unittest.mock import Mock, patch, call

import pytest

from kanyo.detection.event_types import FalconEvent
from kanyo.detection.event_handler import FalconEventHandler


class TestFalconEventHandlerInit:
    def test_default_init(self):
        handler = FalconEventHandler()
        assert handler.notifications is None
        assert handler.clips_dir == "clips"
        assert handler.last_frame is None

    def test_custom_init(self):
        mock_notifs = Mock()
        handler = FalconEventHandler(notifications=mock_notifs, clips_dir="/tmp/clips")
        assert handler.notifications is mock_notifs
        assert handler.clips_dir == "/tmp/clips"

    def test_update_frame(self):
        handler = FalconEventHandler()
        frame = Mock()
        handler.update_frame(frame)
        assert handler.last_frame is frame


class TestFalconEventHandlerArrival:
    def test_arrived_no_notifications(self, caplog):
        """Arrived event without notifications just logs."""
        handler = FalconEventHandler()
        ts = datetime(2026, 2, 26, 10, 0, 0)
        # Should not raise
        handler.handle_event(FalconEvent.ARRIVED, ts, {})

    def test_arrived_with_notifications_no_frame(self):
        """Arrival sends notification with no thumbnail when no frame available."""
        mock_notifs = Mock()
        handler = FalconEventHandler(notifications=mock_notifs)
        ts = datetime(2026, 2, 26, 10, 0, 0)
        handler.handle_event(FalconEvent.ARRIVED, ts, {})
        mock_notifs.send_arrival.assert_called_once_with(ts, None)

    def test_arrived_with_notifications_and_frame(self, tmp_path):
        """Arrival saves thumbnail and sends notification."""
        mock_notifs = Mock()
        import numpy as np

        handler = FalconEventHandler(
            notifications=mock_notifs, clips_dir=str(tmp_path)
        )
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        handler.update_frame(frame)
        ts = datetime(2026, 2, 26, 10, 0, 0)

        with patch("kanyo.detection.event_handler.save_thumbnail", return_value="/tmp/thumb.jpg") as mock_save:
            handler.handle_event(FalconEvent.ARRIVED, ts, {})

        mock_save.assert_called_once()
        mock_notifs.send_arrival.assert_called_once_with(ts, "/tmp/thumb.jpg")


class TestFalconEventHandlerDeparture:
    def test_departed_no_notifications(self):
        """Departed event without notifications just logs."""
        handler = FalconEventHandler()
        ts = datetime(2026, 2, 26, 10, 30, 0)
        handler.handle_event(FalconEvent.DEPARTED, ts, {"visit_duration_seconds": 300})

    def test_departed_with_notifications_no_frame(self):
        """Departure sends notification with no thumbnail."""
        mock_notifs = Mock()
        handler = FalconEventHandler(notifications=mock_notifs)
        ts = datetime(2026, 2, 26, 10, 30, 0)
        handler.handle_event(FalconEvent.DEPARTED, ts, {"visit_duration_seconds": 300})
        mock_notifs.send_departure.assert_called_once_with(ts, None, "5m")

    def test_departed_uses_total_visit_duration_fallback(self):
        """Departure uses total_visit_duration if visit_duration_seconds absent."""
        mock_notifs = Mock()
        handler = FalconEventHandler(notifications=mock_notifs)
        ts = datetime(2026, 2, 26, 10, 30, 0)
        handler.handle_event(FalconEvent.DEPARTED, ts, {"total_visit_duration": 90})
        mock_notifs.send_departure.assert_called_once_with(ts, None, "1m 30s")

    def test_departed_with_frame(self, tmp_path):
        """Departure saves thumbnail and sends notification."""
        mock_notifs = Mock()
        import numpy as np

        handler = FalconEventHandler(
            notifications=mock_notifs, clips_dir=str(tmp_path)
        )
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        handler.update_frame(frame)
        ts = datetime(2026, 2, 26, 10, 30, 0)

        with patch("kanyo.detection.event_handler.save_thumbnail", return_value="/tmp/dep.jpg"):
            handler.handle_event(FalconEvent.DEPARTED, ts, {"visit_duration_seconds": 60})

        mock_notifs.send_departure.assert_called_once_with(ts, "/tmp/dep.jpg", "1m")


class TestFalconEventHandlerRoosting:
    def test_roosting_event_logs(self):
        """Roosting event logs without error."""
        handler = FalconEventHandler()
        ts = datetime(2026, 2, 26, 10, 0, 0)
        # Should not raise
        handler.handle_event(FalconEvent.ROOSTING, ts, {"visit_duration": 3600})
