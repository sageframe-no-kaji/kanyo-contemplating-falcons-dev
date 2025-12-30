"""Tests for custom EVENT log level."""

import logging
import pytest

from kanyo.utils.logger import get_logger, EVENT, setup_logging


class TestEventLogLevel:
    """Test the custom EVENT log level."""

    def test_event_level_value(self):
        """EVENT level should be 25 (between INFO=20 and WARNING=30)."""
        assert EVENT == 25
        assert logging.INFO < EVENT < logging.WARNING

    def test_event_level_name(self):
        """EVENT level should be registered with correct name."""
        assert logging.getLevelName(EVENT) == "EVENT"
        assert logging.getLevelName("EVENT") == EVENT

    def test_logger_has_event_method(self):
        """Logger should have event() method."""
        logger = get_logger("test_event_method")
        assert hasattr(logger, "event")
        assert callable(logger.event)

    def test_event_logs_at_correct_level(self, caplog):
        """event() should log at EVENT level."""
        logger = get_logger("test_event_level")

        with caplog.at_level(EVENT):
            logger.event("Test event message")

        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == EVENT
        assert caplog.records[0].levelname == "EVENT"
        assert caplog.records[0].message == "Test event message"

    def test_event_filtered_by_warning_level(self, caplog):
        """EVENT messages should be filtered when level is WARNING."""
        logger = get_logger("test_event_filtered")

        with caplog.at_level(logging.WARNING):
            logger.event("Should not appear")

        assert len(caplog.records) == 0

    def test_event_appears_at_info_level(self, caplog):
        """EVENT messages should appear when level is INFO."""
        logger = get_logger("test_event_at_info")

        with caplog.at_level(logging.INFO):
            logger.event("Should appear")

        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == EVENT

    def test_event_appears_at_debug_level(self, caplog):
        """EVENT messages should appear when level is DEBUG."""
        logger = get_logger("test_event_at_debug")

        with caplog.at_level(logging.DEBUG):
            logger.event("Should appear at debug")

        assert len(caplog.records) == 1

    def test_level_ordering(self, caplog):
        """All log levels should output in correct order."""
        logger = get_logger("test_ordering")

        with caplog.at_level(logging.DEBUG):
            logger.debug("debug msg")
            logger.info("info msg")
            logger.event("event msg")
            logger.warning("warning msg")
            logger.error("error msg")

        assert len(caplog.records) == 5
        levels = [r.levelno for r in caplog.records]
        assert levels == [logging.DEBUG, logging.INFO, EVENT, logging.WARNING, logging.ERROR]

    def test_event_with_formatting(self, caplog):
        """event() should support string formatting."""
        logger = get_logger("test_event_format")

        with caplog.at_level(EVENT):
            logger.event("Falcon %s at %s", "arrived", "10:30 PM")

        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Falcon arrived at 10:30 PM"

    def test_event_with_extra(self, caplog):
        """event() should support extra kwargs."""
        logger = get_logger("test_event_extra")

        with caplog.at_level(EVENT):
            logger.event("Test message", extra={"custom_field": "value"})

        assert len(caplog.records) == 1
        assert caplog.records[0].custom_field == "value"
