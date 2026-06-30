"""Regression for 021-I: admin clip_service regex must accept both old and
new filename formats so existing recordings remain visible after the format
change.

The admin module itself cannot be imported here (admin/web has no test deps
in requirements-dev.txt — see prior 021-* decisions), so this test re-uses
the same regex source by copying it. If clip_service drifts from this
pattern, the test alongside it should be updated.
"""

import re

# Mirrors the regex in admin/web/app/services/clip_service.py (list_clips +
# list_clips_since). Microseconds slot optional for backward compatibility.
ADMIN_CLIP_PATTERN = re.compile(
    r"falcon_(?P<time>\d{6})(?:_(?P<usec>\d{6}))?_(?P<type>[a-z]+)\."
    r"(?P<ext>mp4|jpg|jpeg|avi|mov|mkv|png)$"
)


class TestRegexAcceptsNewFormat:
    def test_new_format_with_microseconds(self):
        m = ADMIN_CLIP_PATTERN.match("falcon_143025_123456_arrival.mp4")
        assert m is not None
        assert m.group("time") == "143025"
        assert m.group("usec") == "123456"
        assert m.group("type") == "arrival"
        assert m.group("ext") == "mp4"

    def test_new_format_departure_jpg(self):
        m = ADMIN_CLIP_PATTERN.match("falcon_080000_000042_departure.jpg")
        assert m is not None
        assert m.group("type") == "departure"
        assert m.group("usec") == "000042"


class TestRegexBackwardCompatibleWithOldFormat:
    """Existing recordings on disk pre-021-I have no microsecond slot.
    They must still be parsed correctly."""

    def test_old_format_arrival(self):
        m = ADMIN_CLIP_PATTERN.match("falcon_143025_arrival.mp4")
        assert m is not None
        assert m.group("time") == "143025"
        assert m.group("usec") is None
        assert m.group("type") == "arrival"
        assert m.group("ext") == "mp4"

    def test_old_format_visit(self):
        m = ADMIN_CLIP_PATTERN.match("falcon_080000_visit.mp4")
        assert m is not None
        assert m.group("type") == "visit"
        assert m.group("usec") is None


class TestRegexRejectsNonMatches:
    def test_in_progress_tmp_files_rejected(self):
        assert ADMIN_CLIP_PATTERN.match("falcon_143025_arrival.mp4.tmp") is None
        assert ADMIN_CLIP_PATTERN.match("falcon_143025_123456_arrival.mp4.tmp") is None

    def test_log_files_rejected(self):
        assert ADMIN_CLIP_PATTERN.match("falcon_143025_arrival.ffmpeg.log") is None

    def test_non_falcon_prefix_rejected(self):
        assert ADMIN_CLIP_PATTERN.match("other_143025_arrival.mp4") is None

    def test_bad_time_length_rejected(self):
        # 5 digits instead of 6
        assert ADMIN_CLIP_PATTERN.match("falcon_14302_arrival.mp4") is None
        # 7 digits — would incorrectly partial-match if anchor was wrong
        assert ADMIN_CLIP_PATTERN.match("falcon_1430255_arrival.mp4") is None
