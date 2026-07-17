"""Tests for custom EVENT log level, UTC formatting, and DEBUG buffering."""

import logging
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from kanyo.utils.logger import (
    DEFAULT_LEVEL,
    DEFAULT_LOG_FILE,
    EVENT,
    BufferedDebugHandler,
    UTCFormatter,
    get_logger,
    setup_logging,
    setup_logging_from_config,
)


def make_record(level: int, msg: str = "message") -> logging.LogRecord:
    """A bare LogRecord at the given level."""
    return logging.LogRecord("test_logger", level, __file__, 1, msg, None, None)


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


class TestUTCFormatter:
    """Log timestamps are always rendered in UTC."""

    def test_format_time_default_format_is_utc(self):
        """Without a datefmt, formatTime renders UTC as Y-m-d H:M:S."""
        formatter = UTCFormatter()
        record = make_record(logging.INFO)
        record.created = datetime(2026, 1, 1, 12, 30, 45, tzinfo=timezone.utc).timestamp()

        assert formatter.formatTime(record) == "2026-01-01 12:30:45"

    def test_format_time_honors_custom_datefmt(self):
        formatter = UTCFormatter()
        record = make_record(logging.INFO)
        record.created = datetime(2026, 1, 1, 12, 30, 45, tzinfo=timezone.utc).timestamp()

        assert formatter.formatTime(record, datefmt="%H:%M") == "12:30"


class TestBufferedDebugHandler:
    """Smart DEBUG buffering: buffer quietly, flush around events."""

    def make_handler(self, tmp_path, **kwargs) -> tuple[BufferedDebugHandler, "object"]:
        log_file = tmp_path / "kanyo.log"
        handler = BufferedDebugHandler(str(log_file), **kwargs)
        handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
        return handler, log_file

    def test_debug_is_buffered_not_written(self, tmp_path):
        """Outside a capture window, DEBUG goes to the ring buffer only."""
        handler, log_file = self.make_handler(tmp_path)
        try:
            handler.emit(make_record(logging.DEBUG, "quiet context"))
            handler.flush()

            assert "quiet context" not in log_file.read_text()
            assert len(handler._debug_buffer) == 1
        finally:
            handler.close()

    def test_event_flushes_buffered_debug_before_itself(self, tmp_path):
        """An EVENT-or-higher record flushes the DEBUG context ahead of it."""
        handler, log_file = self.make_handler(tmp_path)
        try:
            handler.emit(make_record(logging.DEBUG, "context before"))
            handler.emit(make_record(logging.WARNING, "the event"))

            content = log_file.read_text()
            assert content.index("context before") < content.index("the event")
            assert len(handler._debug_buffer) == 0
        finally:
            handler.close()

    def test_debug_written_directly_during_capture_window(self, tmp_path):
        """After an event, DEBUG passes straight to file for the window."""
        handler, log_file = self.make_handler(tmp_path, capture_window=60.0)
        try:
            handler.emit(make_record(logging.WARNING, "the event"))
            handler.emit(make_record(logging.DEBUG, "context after"))
            handler.flush()

            assert "context after" in log_file.read_text()
            assert len(handler._debug_buffer) == 0
        finally:
            handler.close()

    def test_emit_failure_routes_to_handle_error(self, tmp_path):
        """A formatting failure never propagates — logging must not crash
        the pipeline; the record goes to handleError."""
        handler, _ = self.make_handler(tmp_path)
        try:
            handler.format = Mock(side_effect=ValueError("bad format"))
            handler.handleError = Mock()

            record = make_record(logging.DEBUG, "unformattable")
            handler.emit(record)

            handler.handleError.assert_called_once_with(record)
        finally:
            handler.close()


class TestSetupLogging:
    """setup_logging is idempotent; config values pass through."""

    def test_repeat_setup_updates_level_without_duplicating_handlers(self):
        """A second setup_logging call re-levels the root logger but never
        stacks duplicate handlers."""
        get_logger("test_setup_idempotent")  # guarantees initialization
        root = logging.getLogger()
        handlers_before = list(root.handlers)
        level_before = root.level

        try:
            setup_logging("WARNING")
            assert root.handlers == handlers_before
            assert root.level == logging.WARNING
        finally:
            root.setLevel(level_before)

    def test_setup_logging_from_config_passes_values(self):
        with patch("kanyo.utils.logger.setup_logging") as mock_setup:
            setup_logging_from_config({"log_level": "DEBUG", "log_file": "logs/custom.log"})

        mock_setup.assert_called_once_with(level="DEBUG", log_file="logs/custom.log")

    def test_setup_logging_from_config_defaults(self):
        with patch("kanyo.utils.logger.setup_logging") as mock_setup:
            setup_logging_from_config({})

        mock_setup.assert_called_once_with(level=DEFAULT_LEVEL, log_file=DEFAULT_LOG_FILE)
