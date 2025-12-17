"""
Notification utilities (placeholder).

For now, just logs. Can be extended with:
- Email via smtplib or SendGrid
- SMS via Twilio
- Push via Pushover/ntfy
"""

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


def send_email(to: str, subject: str, body: str) -> bool:
    """Send email notification (placeholder - logs only)."""
    logger.info(f"📧 EMAIL to {to}: {subject}")
    logger.debug(f"Body: {body}")
    # TODO: Implement with smtplib or SendGrid
    return True


def send_sms(to: str, message: str) -> bool:
    """Send SMS notification (placeholder - logs only)."""
    logger.info(f"📱 SMS to {to}: {message[:50]}...")
    # TODO: Implement with Twilio
    return True
