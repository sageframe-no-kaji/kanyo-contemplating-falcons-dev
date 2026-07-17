"""Tests for the creature identity feature (issue #8).

Covers the Creature value object itself, its safe-fallback construction from
config, and — critically — that with DEFAULT config the notification captions
and EVENT log lines are byte-identical to the pre-#8 falcon/🦅 output, so
existing sites see no change.
"""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from kanyo.detection.event_handler import FalconEventHandler
from kanyo.detection.event_types import FalconEvent
from kanyo.utils.config import DEFAULTS
from kanyo.utils.creature import DEFAULT_CREATURE_EMOJI, DEFAULT_CREATURE_NAME, Creature
from kanyo.utils.notifications import NotificationManager

TS = datetime(2026, 7, 16, 14, 30, 25)


def _manager(extra_config=None):
    """Telegram-enabled NotificationManager with controllable creature config."""
    config = {
        "telegram_enabled": True,
        "telegram_channel": "@testchan",
        "notification_cooldown_minutes": 5,
    }
    config.update(extra_config or {})
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "testtoken"}):
        return NotificationManager(config)


class TestCreatureValueObject:
    def test_defaults(self):
        c = Creature()
        assert c.name == "falcon"
        assert c.emoji == "🦅"

    def test_derived_casings(self):
        c = Creature(name="hummingbird", emoji="🐦")
        assert c.title == "Hummingbird"
        assert c.upper == "HUMMINGBIRD"

    def test_default_casings_match_historical_strings(self):
        c = Creature()
        assert c.title == "Falcon"
        assert c.upper == "FALCON"

    def test_empty_name_title_and_upper_do_not_raise(self):
        c = Creature(name="")
        assert c.title == ""
        assert c.upper == ""


class TestCreatureFromConfig:
    def test_missing_keys_use_defaults(self):
        c = Creature.from_config({})
        assert c == Creature()

    def test_none_config_uses_defaults(self):
        c = Creature.from_config(None)
        assert c == Creature()

    def test_custom_values(self):
        c = Creature.from_config({"creature_name": "shark", "creature_emoji": "🦈"})
        assert c.name == "shark"
        assert c.emoji == "🦈"

    def test_values_are_stripped(self):
        c = Creature.from_config({"creature_name": " eagle ", "creature_emoji": " 🦅 "})
        assert c.name == "eagle"
        assert c.emoji == "🦅"

    @pytest.mark.parametrize("bad", ["", "   ", None, 42, ["falcon"]])
    def test_invalid_name_falls_back(self, bad):
        c = Creature.from_config({"creature_name": bad})
        assert c.name == DEFAULT_CREATURE_NAME

    @pytest.mark.parametrize("bad", ["", "   ", None, 42, ["🦅"]])
    def test_invalid_emoji_falls_back(self, bad):
        c = Creature.from_config({"creature_emoji": bad})
        assert c.emoji == DEFAULT_CREATURE_EMOJI

    def test_invalid_values_never_raise(self):
        """Issue #8 acceptance: invalid config never breaks startup."""
        c = Creature.from_config({"creature_name": object(), "creature_emoji": object()})
        assert c == Creature()

    def test_config_defaults_carry_the_keys(self):
        """load_config()-style dicts carry the defaults explicitly."""
        assert DEFAULTS["creature_name"] == DEFAULT_CREATURE_NAME
        assert DEFAULTS["creature_emoji"] == DEFAULT_CREATURE_EMOJI


class TestNotificationCaptions:
    """Captions with default config must be byte-identical to pre-#8 output."""

    def _sent_photo_caption(self, mgr, send):
        with patch.object(mgr, "_send_telegram_photo", return_value=True) as mock_send:
            send(mgr)
        return mock_send.call_args[0][0]

    def test_default_arrival_caption_unchanged(self):
        caption = self._sent_photo_caption(_manager(), lambda m: m.send_arrival(TS, "thumb.jpg"))
        assert caption == "🦅 Falcon arrived at 02:30 PM (stream local)"

    def test_default_departure_caption_unchanged(self):
        caption = self._sent_photo_caption(
            _manager(), lambda m: m.send_departure(TS, "thumb.jpg", "4m 23s")
        )
        assert caption == "👋 Falcon departed at 02:30 PM (visit: 4m 23s, stream local)"

    def test_default_departure_caption_no_duration_unchanged(self):
        caption = self._sent_photo_caption(
            _manager(), lambda m: m.send_departure(TS, "thumb.jpg", None)
        )
        assert caption == "👋 Falcon departed at 02:30 PM (stream local)"

    def test_default_count_up_message_unchanged(self):
        mgr = _manager()
        with patch.object(mgr, "_send_telegram_text", return_value=True) as mock_send:
            mgr.send_count_change(TS, 1, 2)
        assert (
            mock_send.call_args[0][0]
            == "🦅 Another falcon arrived — 2 birds in view (02:30 PM stream local)"
        )

    def test_default_count_down_message_unchanged(self):
        mgr = _manager()
        with patch.object(mgr, "_send_telegram_text", return_value=True) as mock_send:
            mgr.send_count_change(TS, 2, 1)
        assert (
            mock_send.call_args[0][0]
            == "👋 One falcon left — 1 bird still in view (02:30 PM stream local)"
        )

    def test_custom_creature_arrival_caption(self):
        mgr = _manager({"creature_name": "eagle", "creature_emoji": "🦅"})
        caption = self._sent_photo_caption(mgr, lambda m: m.send_arrival(TS, "thumb.jpg"))
        assert caption == "🦅 Eagle arrived at 02:30 PM (stream local)"

    def test_custom_creature_departure_caption(self):
        mgr = _manager({"creature_name": "shark", "creature_emoji": "🦈"})
        caption = self._sent_photo_caption(
            mgr, lambda m: m.send_departure(TS, "thumb.jpg", "4m 23s")
        )
        assert caption == "👋 Shark departed at 02:30 PM (visit: 4m 23s, stream local)"

    def test_custom_creature_count_messages(self):
        mgr = _manager({"creature_name": "shark", "creature_emoji": "🦈"})
        with patch.object(mgr, "_send_telegram_text", return_value=True) as mock_send:
            mgr.send_count_change(TS, 1, 2)
            mgr.send_count_change(TS, 2, 1)
        up, down = (c.args[0] for c in mock_send.call_args_list)
        assert up == "🦈 Another shark arrived — 2 birds in view (02:30 PM stream local)"
        assert down == "👋 One shark left — 1 bird still in view (02:30 PM stream local)"


class TestEventHandlerLogLines:
    """EVENT log lines with default creature must match pre-#8 output."""

    def _event_lines(self, handler, event, metadata, caplog):
        import logging

        with caplog.at_level(logging.INFO):
            handler.handle_event(event, TS, metadata)
        return [r.getMessage() for r in caplog.records]

    def test_default_arrived_line_unchanged(self, caplog):
        lines = self._event_lines(FalconEventHandler(), FalconEvent.ARRIVED, {}, caplog)
        assert "🦅 FALCON ARRIVED at 02:30:25 PM (stream local)" in lines

    def test_default_departed_line_unchanged(self, caplog):
        lines = self._event_lines(
            FalconEventHandler(), FalconEvent.DEPARTED, {"visit_duration_seconds": 263}, caplog
        )
        assert "🦅 FALCON DEPARTED at 02:30:25 PM (4m 23s visit, stream local)" in lines

    def test_default_roosting_line_unchanged(self, caplog):
        lines = self._event_lines(
            FalconEventHandler(), FalconEvent.ROOSTING, {"visit_duration_seconds": 1800}, caplog
        )
        assert "🏠 FALCON ROOSTING - settled for long-term stay (visit: 30m)" in lines

    def test_custom_creature_arrived_line(self, caplog):
        handler = FalconEventHandler(creature=Creature(name="eagle", emoji="🦅"))
        lines = self._event_lines(handler, FalconEvent.ARRIVED, {}, caplog)
        assert "🦅 EAGLE ARRIVED at 02:30:25 PM (stream local)" in lines

    def test_custom_creature_departed_and_roosting_lines(self, caplog):
        handler = FalconEventHandler(creature=Creature(name="shark", emoji="🦈"))
        lines = self._event_lines(
            handler, FalconEvent.DEPARTED, {"visit_duration_seconds": 60}, caplog
        )
        assert "🦈 SHARK DEPARTED at 02:30:25 PM (1m visit, stream local)" in lines
        lines = self._event_lines(
            handler, FalconEvent.ROOSTING, {"visit_duration_seconds": 1800}, caplog
        )
        assert "🏠 SHARK ROOSTING - settled for long-term stay (visit: 30m)" in lines

    def test_handler_notifications_still_called(self):
        """Creature threading must not disturb the notification wiring."""
        mock_notifs = Mock()
        handler = FalconEventHandler(
            notifications=mock_notifs, creature=Creature(name="eagle", emoji="🦅")
        )
        handler.handle_event(FalconEvent.ARRIVED, TS, {})
        mock_notifs.send_arrival.assert_called_once_with(TS, None)


class TestTemplateDocumentsCreatureKeys:
    def test_config_template_carries_default_creature_keys(self):
        """configs/config.template.yaml documents the keys with safe defaults."""
        from pathlib import Path

        import yaml

        template = Path(__file__).parent.parent / "configs" / "config.template.yaml"
        cfg = yaml.safe_load(template.read_text())
        assert cfg["creature_name"] == DEFAULT_CREATURE_NAME
        assert cfg["creature_emoji"] == DEFAULT_CREATURE_EMOJI
