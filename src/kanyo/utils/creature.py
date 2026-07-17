"""
Per-stream creature identity for logs and Telegram notifications (issue #8).

Streams monitor different creatures — falcons, eagles, hummingbirds, sharks.
The creature name and emoji used in notification messages and EVENT log lines
come from two per-stream config keys:

    creature_name:  "falcon"   (default)
    creature_emoji: "🦅"       (default)

Defaults reproduce the pre-#8 output byte-for-byte, so existing sites see no
change. Invalid values (wrong type, empty/whitespace) fall back to the
defaults with a warning — creature config can never break startup or
notifications.

Deliberately NOT creature-configurable: the "BUFFER-BASED FALCON MONITOR"
startup banner. The admin log service finds session starts by matching that
exact marker (admin/web/app/services/log_service.py `_find_last_startup`),
so it stays a fixed parse surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CREATURE_NAME = "falcon"
DEFAULT_CREATURE_EMOJI = "🦅"


@dataclass(frozen=True)
class Creature:
    """The creature a stream watches: lowercase name + emoji, with derived casings."""

    name: str = DEFAULT_CREATURE_NAME
    emoji: str = DEFAULT_CREATURE_EMOJI

    @property
    def title(self) -> str:
        """Sentence-case name for message text ("Falcon arrived at ...")."""
        return self.name[:1].upper() + self.name[1:]

    @property
    def upper(self) -> str:
        """Upper-case name for EVENT log identifiers ("FALCON ARRIVED")."""
        return self.name.upper()

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "Creature":
        """
        Build a Creature from a config dict with safe fallbacks.

        Missing keys use the defaults. Invalid values (non-string, empty,
        whitespace-only) log a warning and fall back — never raise (issue #8:
        invalid config must not break startup or notifications).
        """
        cfg = config or {}

        name = cfg.get("creature_name", DEFAULT_CREATURE_NAME)
        if not isinstance(name, str) or not name.strip():
            logger.warning(
                f"⚠️  Invalid creature_name {name!r} — "
                f"falling back to '{DEFAULT_CREATURE_NAME}'"
            )
            name = DEFAULT_CREATURE_NAME

        emoji = cfg.get("creature_emoji", DEFAULT_CREATURE_EMOJI)
        if not isinstance(emoji, str) or not emoji.strip():
            logger.warning(
                f"⚠️  Invalid creature_emoji {emoji!r} — "
                f"falling back to '{DEFAULT_CREATURE_EMOJI}'"
            )
            emoji = DEFAULT_CREATURE_EMOJI

        return cls(name=name.strip(), emoji=emoji.strip())
