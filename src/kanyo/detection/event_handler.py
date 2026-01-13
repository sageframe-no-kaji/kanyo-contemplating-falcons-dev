"""
Event handler for falcon detection events.

Handles falcon state machine events with notifications and thumbnails.
"""

from datetime import datetime

from kanyo.detection.event_types import FalconEvent
from kanyo.utils.logger import get_logger
from kanyo.utils.notifications import NotificationManager
from kanyo.utils.output import save_thumbnail, format_duration

logger = get_logger(__name__)


class FalconEventHandler:
    """
    Handles falcon state machine events.

    Routes events to appropriate actions: notifications, thumbnails, and logging.
    Separated from RealtimeMonitor to keep orchestration clean.
    """

    def __init__(
        self,
        notifications: NotificationManager | None = None,
        clips_dir: str = "clips",
    ):
        """
        Initialize event handler.

        Args:
            notifications: Optional notification manager for alerts
            clips_dir: Base directory for saving thumbnails
        """
        self.notifications = notifications
        self.clips_dir = clips_dir
        self.last_frame = None  # Store last frame for thumbnails

    def update_frame(self, frame):
        """Update the stored frame for thumbnail generation."""
        self.last_frame = frame

    def handle_event(
        self,
        event_type: FalconEvent,
        timestamp: datetime,
        metadata: dict,
    ) -> None:
        """
        Handle falcon state machine events.

        Routes events from state machine to appropriate actions:
        notifications, thumbnails, and clip creation triggers.

        Args:
            event_type: Type of falcon event
            timestamp: When the event occurred
            metadata: Additional event data (duration, counts, etc.)
        """
        if event_type == FalconEvent.ARRIVED:
            logger.event(f"🦅 FALCON ARRIVED at {timestamp.strftime('%I:%M:%S %p')} (stream local)")

            # Send arrival notification
            if self.notifications:
                thumb_path = None
                if self.last_frame is not None:
                    thumb_path = save_thumbnail(
                        self.last_frame,
                        self.clips_dir,
                        timestamp,
                        "arrival",
                    )
                self.notifications.send_arrival(timestamp, thumb_path)

        elif event_type == FalconEvent.DEPARTED:
            # State machine provides visit_duration_seconds or total_visit_duration
            duration = metadata.get("visit_duration_seconds") or metadata.get(
                "total_visit_duration", 0
            )
            duration_str = format_duration(duration)

            logger.event(
                f"🦅 FALCON DEPARTED at {timestamp.strftime('%I:%M:%S %p')} "
                f"({duration_str} visit, stream local)"
            )

            # Send departure notification
            if self.notifications:
                thumb_path = None
                if self.last_frame is not None:
                    thumb_path = save_thumbnail(
                        self.last_frame,
                        self.clips_dir,
                        timestamp,
                        "departure",
                    )
                self.notifications.send_departure(timestamp, thumb_path, duration_str)

        elif event_type == FalconEvent.ROOSTING:
            duration_str = format_duration(metadata.get("visit_duration", 0))
            logger.event(f"🏠 FALCON ROOSTING - settled for long-term stay (visit: {duration_str})")
