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

from kanyo.utils.creature import Creature
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
                - telegram_enabled (bool): Enable/disable Telegram public alerts
                - ntfy_admin_enabled (bool): Enable/disable ntfy admin errors
                - notification_cooldown_minutes (int): Cooldown period

        Env vars:
            - TELEGRAM_BOT_TOKEN: Bot token for Telegram API
            - TELEGRAM_CHANNEL: Channel ID or @username
            - NTFY_ADMIN_TOPIC: Topic for admin/error notifications
        """
        self.telegram_enabled = bool(config.get("telegram_enabled", False))
        self.ntfy_admin_enabled = bool(config.get("ntfy_admin_enabled", False))
        self.cooldown_minutes = int(config.get("notification_cooldown_minutes", 5))
        self.last_departure_time: datetime | None = None

        # Creature identity for message text (issue #8). Defaults reproduce
        # the historical falcon/🦅 wording byte-for-byte.
        self.creature = Creature.from_config(config)

        # Load credentials - token from env (secret), channel/topic from config or env
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_channel = config.get("telegram_channel") or os.getenv("TELEGRAM_CHANNEL", "")
        self.ntfy_admin_topic = config.get("ntfy_topic") or os.getenv("NTFY_ADMIN_TOPIC", "")

        # Validate Telegram configuration
        if self.telegram_enabled:
            if not self.telegram_token or not self.telegram_channel:
                logger.warning(
                    "⚠️  telegram_enabled is True but TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL "
                    "not set - disabling Telegram notifications"
                )
                self.telegram_enabled = False
            else:
                logger.info(
                    f"📧 Telegram enabled: {self.telegram_channel}, "
                    f"cooldown={self.cooldown_minutes}min"
                )

        # Validate ntfy admin configuration
        if self.ntfy_admin_enabled:
            if self.ntfy_admin_topic:
                logger.info(f"🔧 Admin errors enabled → ntfy: {self.ntfy_admin_topic}")
            else:
                logger.warning("⚠️  ntfy_admin_enabled is True but NTFY_ADMIN_TOPIC not set")
                self.ntfy_admin_enabled = False

    def send_arrival(self, timestamp: datetime, thumbnail_path: Path | str | None) -> bool:
        """
        Send arrival notification to Telegram if cooldown period has passed.

        Args:
            timestamp: Time of falcon arrival
            thumbnail_path: Path to arrival thumbnail image

        Returns:
            True if notification sent, False if suppressed or failed
        """
        if not self.telegram_enabled:
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
        caption = f"{self.creature.emoji} {self.creature.title} arrived at {ts_str} (stream local)"

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
        if not self.telegram_enabled:
            return False

        # Build message
        ts_str = timestamp.strftime("%I:%M %p")
        creature = self.creature.title
        if visit_duration_str:
            caption = (
                f"👋 {creature} departed at {ts_str} (visit: {visit_duration_str}, stream local)"
            )
        else:
            caption = f"👋 {creature} departed at {ts_str} (stream local)"

        # Always send departure (no cooldown check)
        success = self._send_telegram_photo(caption, thumbnail_path)

        # Start cooldown after departure
        if success:
            self.last_departure_time = timestamp

        return success

    def send_count_change(self, timestamp: datetime, old_count: int, new_count: int) -> bool:
        """
        Send a bird-count change notification to Telegram (issue #3).

        Fires only for confirmed count changes while the nest is occupied
        (1→2, 2→1, …) — the 0-boundary changes are the arrival/departure
        notifications' territory. Text-only, no cooldown interaction: count
        changes are already gated by the count confirmation window and folded
        into summaries by the significance filter's activity damping.

        Args:
            timestamp: When the change confirmed (stream local)
            old_count: The previously confirmed count
            new_count: The newly confirmed count

        Returns:
            True if sent, False if disabled or failed
        """
        if not self.telegram_enabled:
            return False

        ts_str = timestamp.strftime("%I:%M %p")
        birds = "bird" if new_count == 1 else "birds"
        if new_count > old_count:
            message = (
                f"{self.creature.emoji} Another {self.creature.name} arrived — "
                f"{new_count} {birds} in view ({ts_str} stream local)"
            )
        else:
            message = (
                f"👋 One {self.creature.name} left — {new_count} {birds} still in view "
                f"({ts_str} stream local)"
            )
        return self._send_telegram_text(message)

    def send_activity_summary(self, message: str) -> bool:
        """
        Send an activity summary to Telegram (significance filter damped mode, ho-09).

        Summaries replace individual arrival/departure notifications while the
        arrival rate is above the damping threshold. Text-only — no photo, and
        no cooldown interaction (summaries are already rate-limited to one per
        damping window by the filter).

        Args:
            message: Summary text (e.g. "9 visits in the last hour, median 25s")

        Returns:
            True if sent, False if disabled or failed
        """
        if not self.telegram_enabled:
            return False
        return self._send_telegram_text(message)

    def _send_telegram_text(self, text: str) -> bool:
        """
        Send a text-only message to the Telegram channel.

        Args:
            text: Message text

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                "chat_id": self.telegram_channel,
                "text": text,
            }
            resp = requests.post(url, data=data, timeout=10)

            if resp.status_code == 200:
                logger.event(f"📧 Telegram sent: {text}")
                return True

            error_msg = resp.text[:200]
            logger.error(f"❌ Telegram API error {resp.status_code}: {error_msg}")
            self._send_admin_error(f"Telegram delivery failed: {resp.status_code}")
            return False

        except requests.Timeout:
            logger.error(f"❌ Telegram timeout (10s): {text}")
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
                logger.event(f"📧 Telegram sent: {caption}")
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

    def send_system_alert(self, message: str, title: str = "Kanyo System Alert") -> None:
        """
        Send system/admin alert to ntfy admin topic (NOT to public Telegram).

        Use this for connection issues, errors, or other admin-only notifications.

        Args:
            message: Alert message text
            title: Notification title
        """
        self._send_admin_notification(message, title)

    def _send_admin_error(self, message: str) -> None:
        """
        Send error notification to ntfy admin topic (text only).

        Args:
            message: Error message text
        """
        self._send_admin_notification(message, "Kanyo Error")

    def _send_admin_notification(self, message: str, title: str) -> None:
        """
        Internal method to send notifications to ntfy admin topic.

        Args:
            message: Notification message text
            title: Notification title
        """
        if not self.ntfy_admin_enabled or not self.ntfy_admin_topic:
            return

        try:
            url = f"https://ntfy.sh/{self.ntfy_admin_topic}"
            headers = {
                "Title": title,
                "Content-Type": "text/plain; charset=utf-8",
            }
            data = message.encode("utf-8")
            resp = requests.post(url, data=data, headers=headers, timeout=5)

            if resp.status_code >= 200 and resp.status_code < 300:
                logger.debug(f"🔧 Admin notification sent to ntfy: {message}")
            else:
                logger.warning(f"⚠️  Failed to send admin notification to ntfy: {resp.status_code}")

        except Exception as e:
            logger.warning(f"⚠️  Could not send admin notification to ntfy: {e}")
