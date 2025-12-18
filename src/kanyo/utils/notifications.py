"""
Notification utilities for ntfy.sh push notifications.

NotificationManager encapsulates all notification logic with smart cooldown
to prevent spam while ensuring complete arrival+departure pairs are sent.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


class NotificationManager:
    """
    Manages push notifications via ntfy.sh with smart cooldown logic.

    Cooldown applies ONLY to arrival notifications:
    - Arrival notifications suppressed during cooldown period
    - Departure notifications always sent (complete the visit story)
    - Cooldown starts AFTER each departure notification

    This prevents spam from repeated visits while ensuring you see
    complete arrival+departure notification pairs for genuine visits.
    """

    def __init__(self, config: dict[str, Any]):
        """
        Initialize notification manager from config.

        Args:
            config: Dictionary with keys:
                - ntfy_enabled (bool): Enable/disable notifications
                - ntfy_topic (str): Topic name for ntfy.sh
                - notification_cooldown_minutes (int): Cooldown period
        """
        self.enabled = bool(config.get("ntfy_enabled", False))
        self.topic = config.get("ntfy_topic", "")
        self.cooldown_minutes = int(config.get("notification_cooldown_minutes", 5))
        self.last_departure_time: datetime | None = None

        if self.enabled and not self.topic:
            logger.warning("⚠️  ntfy_enabled is True but ntfy_topic is empty - disabling notifications")
            self.enabled = False

        if self.enabled:
            logger.info(
                f"📧 Notifications enabled: topic='{self.topic}', "
                f"cooldown={self.cooldown_minutes}min"
            )

    def send_arrival(self, timestamp: datetime, thumbnail_path: Path | str | None) -> bool:
        """
        Send arrival notification if cooldown period has passed.

        Args:
            timestamp: Time of falcon arrival
            thumbnail_path: Path to arrival thumbnail image

        Returns:
            True if notification sent, False if suppressed or failed
        """
        if not self.enabled:
            return False

        # Check cooldown
        if self.last_departure_time is not None:
            elapsed_seconds = (timestamp - self.last_departure_time).total_seconds()
            cooldown_seconds = self.cooldown_minutes * 60

            if elapsed_seconds < cooldown_seconds:
                remaining_seconds = cooldown_seconds - elapsed_seconds
                remaining_minutes = int(remaining_seconds / 60)
                logger.info(
                    f"🔇 Arrival notification suppressed "
                    f"(cooldown active: {remaining_minutes}m remaining)"
                )
                return False

        # Build message
        ts_str = timestamp.strftime("%I:%M %p")
        title = "Falcon Arrived"
        message = f"Detected at {ts_str}"

        return self._send_ntfy(title, message, thumbnail_path)

    def send_departure(
        self,
        timestamp: datetime,
        thumbnail_path: Path | str | None,
        visit_duration_str: str | None = None,
    ) -> bool:
        """
        Send departure notification (always, regardless of cooldown).

        After sending, starts new cooldown period.

        Args:
            timestamp: Time of falcon departure
            thumbnail_path: Path to departure thumbnail image
            visit_duration_str: Human-readable visit duration (e.g., "4m 23s")

        Returns:
            True if notification sent, False if failed
        """
        if not self.enabled:
            return False

        # Build message
        ts_str = timestamp.strftime("%I:%M %p")
        title = "Falcon Departed"
        if visit_duration_str:
            message = f"Left at {ts_str} (visit: {visit_duration_str})"
        else:
            message = f"Left at {ts_str}"

        # Always send departure (no cooldown check)
        success = self._send_ntfy(title, message, thumbnail_path)

        # Start cooldown after departure
        if success:
            self.last_departure_time = timestamp

        return success

    def _send_ntfy(
        self, title: str, message: str, thumbnail_path: Path | str | None
    ) -> bool:
        """
        Send notification to ntfy.sh with optional image attachment.

        Args:
            title: Notification title
            message: Notification message body
            thumbnail_path: Optional path to image attachment

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            url = f"https://ntfy.sh/{self.topic}"
            headers = {"Title": title}

            # Prepare data (image or text)
            data: bytes | str
            if thumbnail_path:
                thumb = Path(thumbnail_path)
                if thumb.exists():
                    headers["Filename"] = thumb.name
                    headers["X-Message"] = message
                    data = thumb.read_bytes()
                else:
                    logger.warning(
                        f"⚠️  Thumbnail not found: {thumbnail_path} - sending text only"
                    )
                    data = message
            else:
                data = message

            # Send request
            resp = requests.post(url, data=data, headers=headers, timeout=10)

            if 200 <= resp.status_code < 300:
                logger.info(f"📧 Notification sent: {title}")
                return True
            else:
                logger.error(
                    f"❌ ntfy.sh returned {resp.status_code} for '{title}': "
                    f"{resp.text[:200]}"
                )
                return False

        except requests.Timeout:
            logger.error(f"❌ Notification timeout for '{title}' (10s)")
            return False
        except requests.ConnectionError as e:
            logger.error(f"❌ Connection error sending '{title}': {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error sending '{title}': {e}")
            return False
