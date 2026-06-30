"""Regression tests for 021-G: parse_stream_timezone is the public shared name
that admin web service and any other consumer can reuse.

Core parses correctly for IANA names, mapped legacy offsets (+11:00 etc.),
unmapped offsets (returns fixed-offset tzinfo, not UTC), and invalid input
(falls back to UTC without raising).
"""

from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from kanyo.utils.config import OFFSET_TO_TZ, parse_stream_timezone


class TestParseStreamTimezone:
    def test_iana_name_resolves_to_zoneinfo(self):
        tz = parse_stream_timezone("America/New_York")
        assert isinstance(tz, ZoneInfo)
        assert tz.key == "America/New_York"

    def test_utc_string(self):
        tz = parse_stream_timezone("UTC")
        assert isinstance(tz, ZoneInfo)
        assert tz.key == "UTC"

    def test_plus_zero_normalised_to_utc(self):
        tz = parse_stream_timezone("+00:00")
        assert isinstance(tz, ZoneInfo)
        assert tz.key == "UTC"

    def test_mapped_legacy_offset_plus_11(self):
        """+11:00 maps to Australia/Sydney via OFFSET_TO_TZ."""
        tz = parse_stream_timezone("+11:00")
        assert isinstance(tz, ZoneInfo)
        assert tz.key == "Australia/Sydney"

    def test_mapped_legacy_offset_minus_5(self):
        tz = parse_stream_timezone("-05:00")
        assert isinstance(tz, ZoneInfo)
        assert tz.key == "America/New_York"

    def test_unmapped_offset_returns_fixed_offset_not_utc(self):
        """Unmapped offsets must NOT silently become UTC — they get a real
        fixed-offset tzinfo so timestamps render correctly."""
        # +04:00 is not in OFFSET_TO_TZ
        tz = parse_stream_timezone("+04:00")
        # Should be a datetime.timezone, not UTC
        assert isinstance(tz, timezone)
        now = datetime.now(tz)
        assert now.utcoffset() == timedelta(hours=4)

    def test_invalid_string_falls_back_to_utc_no_raise(self):
        tz = parse_stream_timezone("not-a-timezone")
        # Either ZoneInfo("UTC") or timezone.utc — both are valid fallbacks
        assert datetime.now(tz).utcoffset() == timedelta(0)

    def test_empty_string_returns_utc(self):
        tz = parse_stream_timezone("")
        assert datetime.now(tz).utcoffset() == timedelta(0)


class TestOffsetToTzMapShape:
    """Pin the shape of OFFSET_TO_TZ so admin's DUPLICATE copy stays consistent."""

    def test_known_offsets_present(self):
        # If any of these go missing, the admin DUPLICATE map is also out of date.
        for offset in ["+11:00", "-05:00", "-08:00", "+00:00"]:
            assert offset in OFFSET_TO_TZ, (
                f"{offset} missing from core OFFSET_TO_TZ — also remove from admin's"
            )

    def test_values_are_resolvable_iana(self):
        for offset, name in OFFSET_TO_TZ.items():
            ZoneInfo(name)  # raises if invalid; this is the assertion
