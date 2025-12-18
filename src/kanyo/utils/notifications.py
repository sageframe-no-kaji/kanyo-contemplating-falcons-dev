"""
Notification utilities for Telegram (public) and ntfy (admin/errors).

NotificationManager routes notifications to appropriate channels:
- Telegram: Public alerts (arrival/departure) with images
- ntfy: Admin errors only (text)

Cooldown logic prevents spam while ensuring complete arrival+departure pairs.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


class NotificationManager:
    """
    Manages push notifications via Telegram (public) and ntfy (admin/errors).

    Channel routing:
    - Telegram: Falcon arrival/departure alerts with images (public)
    - ntfy admin: Errors and delivery failures only (text)

    Cooldown applies ONLY to arrival notifications:
    - Arrival notifications suppressed during cooldown period
    - Departure notifications always sent (complete the visit story)
    - Cooldown starts AFTER each departure notification
    """

    def __init__(self, config: dict[str, Any]):
        """
        Initialize notification manager from config + env.

        Args:
            config: Dictionary with keys:
                - ntfy_enabled (bool): Enable/disable notifications
                - notification_cooldown_minutes (int): Cooldown period

        Env vars:
            - TELEGRAM_BOT_TOKEN: Bot token for Telegram API
            - TELEGRAM_CHANNEL: Channel ID or @username
            - NTFY_ADMIN_TOPIC: Topic for admin/error notifications
        """
        self.enabled = bool(config.get("ntfy_enabled", False))
        self.cooldown_minutes = int(config.get("notification_cooldown_minutes", 5))
        self.last_departure_time: datetime | None = None

        # Load credentials from environment
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_channel = os.getenv("TELEGRAM_CHANNEL", "")
        self.ntfy_admin_topic = os.getenv("NTFY_ADMIN_TOPIC", "")

        # Validate configuration
        if self.enabled:
            if not self.telegram_token or not self.telegram_channel:
                logger.warning(
                    "⚠️  ntfy_enabled is True but TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL "
                    "not set - disabling notifications"
                )
                self.enabled = False
            else:
                logger.info(
                    f"📧 Notifications enabled: Telegram={self.telegram_channel}, "
                    f"cooldown={self.cooldown_minutes}min"
                )
                if self.ntfy_admin_topic:
                    logger.info(f"🔧 Admin errors → ntfy topic: {self.ntfy_admin_topic}")
                else:
                    logger.warning("⚠️  NTFY_ADMIN_TOPIC not set - error reporting disabled")

    def send_arrival(self, timestamp: datetime, thumbnail_path: Path | str | None) -> bool:
        """
        Send arrival notification to Telegram if cooldown period has passed.

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
        caption = f"🦅 Falcon arrived at {ts_str}"

        return self._send_telegram_photo(caption, thumbnail_path)

    def send_departure(
        self,
        timestamp: datetime,
        thumbnail_path: Path | str | None,
        visit_duration_str: str | None = None,
    ) -> bool:
        """
        Send departure notification to Telegram (always, regardless of cooldown).

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
        if visit_duration_str:
            caption = f"👋 Falcon departed at {ts_str} (visit: {visit_duration_str})"
        else:
            caption = f"👋 Falcon departed at {ts_str}"

        # Always send departure (no cooldown check)
        success = self._send_telegram_photo(caption, thumbnail_path)

        # Start cooldown after departure
        if success:
            self.last_departure_time = timestamp

        return success

    def _send_telegram_photo(self, caption: str, photo_path: Path | str | None) -> bool:
        """
        Send photo message to Telegram channel.

        Args:
            caption: Photo caption text
            photo_path: Path to image file

        Returns:
            True if sent successfully, False otherwise
        """
        if not photo_path:
            logger.error("❌ Telegram requires image - no photo_path provided")
            self._send_admin_error("Telegram alert failed: missing image")
            return False

        photo = Path(photo_path)
        if not photo.exists():
            logger.error(f"❌ Photo not found: {photo_path}")
            self._send_admin_error(f"Telegram alert failed: image not found ({photo.name})")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendPhoto"

            with photo.open("rb") as f:
                files = {"photo": f}
                data = {
                    "chat_id": self.telegram_channel,
                    "caption": caption,
                }
                resp = requests.post(url, data=data, files=files, timeout=10)

            if resp.status_code == 200:
                logger.info(f"📧 Telegram sent: {caption}")
                return True
            else:
                error_msg = resp.text[:200]
                logger.error(f"❌ Telegram API error {resp.status_code}: {error_msg}")
                self._send_admin_error(f"Telegram delivery failed: {resp.status_code}")
                return False

        except requests.Timeout:
            logger.error(f"❌ Telegram timeout (10s): {caption}")
            self._send_admin_error("Telegram delivery timeout")
            return False
        except requests.ConnectionError as e:
            logger.error(f"❌ Telegram connection error: {e}")
            self._send_admin_error(f"Telegram connection failed: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Telegram unexpected error: {e}")
            self._send_admin_error(f"Telegram error: {e}")
            return False

    def _send_admin_error(self, message: str) -> None:
        """
        Send error notification to ntfy admin topic (text only).

        Args:
            message: Error message text
        """
        if not self.ntfy_admin_topic:
            return

        try:
            url = f"https://ntfy.sh/{self.ntfy_admin_topic}"
            headers = {
                "Title": "Kanyo Error",  # Removed emoji to avoid encoding issues
                "Content-Type": "text/plain; charset=utf-8"
            }
            data = message.encode("utf-8")
            resp = requests.post(url, data=data, headers=headers, timeout=5)

            if resp.status_code >= 200 and resp.status_code < 300:
                logger.debug(f"🔧 Admin error sent to ntfy: {message}")
            else:
                logger.warning(f"⚠️  Failed to send admin error to ntfy: {resp.status_code}")

        except Exception as e:
            logger.warning(f"⚠️  Could not send admin error to ntfy: {e}")

