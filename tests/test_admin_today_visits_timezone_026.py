"""Regression for 026: admin get_today_visits must count arrivals in the
STREAM-local dated folder, and file-browser mtimes must render in the
stream's timezone — not the server's (UTC in production containers).

Completes 021-G, which fixed get_last_event the same way but missed
get_today_visits and the stream_files mtime render path.

The admin module itself cannot be imported here (admin/web has no test
deps in the test venv — see the 021-I precedent in
test_admin_filename_regex_021i.py), so these tests mirror the logic.

# MIRROR — keep in sync with admin/web/app/services/clip_service.py
# (get_stream_today / get_today_visits) and
# admin/web/app/routers/pages.py (stream_files mtime rendering).
Core's parse_stream_timezone is the same parser admin's DUPLICATE copy
mirrors (pinned by test_parse_stream_timezone_021g.py), so it stands in
for admin's get_stream_timezone here.
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from kanyo.utils.config import parse_stream_timezone

# Mirrors the clip filename regex in admin/web/app/services/clip_service.py.
ADMIN_CLIP_PATTERN = re.compile(
    r"falcon_(?P<time>\d{6})(?:_(?P<usec>\d{6}))?_(?P<type>[a-z]+)\."
    r"(?P<ext>mp4|jpg|jpeg|avi|mov|mkv|png)$"
)

# A fixed instant just after the UTC date rolls over: 2026-07-16 01:00 UTC.
# Harvard (EDT, UTC-4) is still on 2026-07-15 at 21:00; Sydney (+11:00 legacy
# offset -> Australia/Sydney, AEST +10 in July) is on 2026-07-16 at 11:00.
BOUNDARY_INSTANT_UTC = datetime(2026, 7, 16, 1, 0, 0, tzinfo=timezone.utc)


def stream_today(stream_timezone: str, now_utc: datetime) -> str:
    """Mirror of clip_service.get_stream_today, parameterized on the instant.

    Admin computes datetime.now(tz).strftime("%Y-%m-%d"); with a pinned
    instant that is now_utc.astimezone(tz).
    """
    tz = parse_stream_timezone(stream_timezone)
    return now_utc.astimezone(tz).strftime("%Y-%m-%d")


def count_today_arrivals(clips_path: Path, stream_timezone: str, now_utc: datetime) -> int:
    """Mirror of clip_service.get_today_visits date selection + counting."""
    date_path = clips_path / stream_today(stream_timezone, now_utc)
    if not date_path.exists():
        return 0
    count = 0
    for clip_file in date_path.iterdir():
        match = ADMIN_CLIP_PATTERN.match(clip_file.name)
        if match and match.group("type") == "arrival":
            count += 1
    return count


class TestStreamLocalDateSelection:
    def test_harvard_evening_is_previous_stream_local_date(self):
        """At 01:00 UTC the naive-UTC date is 07-16, but Harvard is still
        on 07-15 — the exact window where the old code showed 0 visits."""
        assert stream_today("America/New_York", BOUNDARY_INSTANT_UTC) == "2026-07-15"

    def test_harvard_legacy_offset_matches_iana(self):
        assert stream_today("-05:00", BOUNDARY_INSTANT_UTC) == "2026-07-15"

    def test_sydney_matches_utc_date_here(self):
        assert stream_today("+11:00", BOUNDARY_INSTANT_UTC) == "2026-07-16"

    def test_sydney_ahead_of_utc_in_utc_afternoon(self):
        """Sydney's ~10h/day wrong window: UTC afternoon, Sydney next day."""
        instant = datetime(2026, 7, 15, 16, 0, 0, tzinfo=timezone.utc)
        assert stream_today("+11:00", instant) == "2026-07-16"
        # Naive-UTC would read 2026-07-15 — the wrong folder.
        assert instant.strftime("%Y-%m-%d") == "2026-07-15"

    def test_utc_default_unchanged(self):
        assert stream_today("UTC", BOUNDARY_INSTANT_UTC) == "2026-07-16"


class TestTodayVisitsCountsStreamLocalFolder:
    def _build_clips_tree(self, root: Path) -> Path:
        """Arrivals live in the Harvard-dated folder; the naive-UTC-dated
        folder exists but is empty (the folder the old code read)."""
        clips = root / "clips"
        harvard_day = clips / "2026-07-15"
        harvard_day.mkdir(parents=True)
        (harvard_day / "falcon_140000_123456_arrival.mp4").touch()
        (harvard_day / "falcon_183000_654321_arrival.mp4").touch()
        (harvard_day / "falcon_190000_000001_departure.mp4").touch()
        (clips / "2026-07-16").mkdir()  # empty decoy: tomorrow in UTC terms
        return clips

    def test_stream_local_count_is_correct(self, tmp_path):
        clips = self._build_clips_tree(tmp_path)
        count = count_today_arrivals(clips, "America/New_York", BOUNDARY_INSTANT_UTC)
        assert count == 2

    def test_naive_utc_date_would_return_zero(self, tmp_path):
        """Documents the bug being fixed: the naive-UTC folder is empty."""
        clips = self._build_clips_tree(tmp_path)
        count = count_today_arrivals(clips, "UTC", BOUNDARY_INSTANT_UTC)
        assert count == 0

    def test_missing_folder_returns_zero(self, tmp_path):
        clips = tmp_path / "clips"
        clips.mkdir()
        assert count_today_arrivals(clips, "America/New_York", BOUNDARY_INSTANT_UTC) == 0


class TestMtimeRendering:
    """Mirror of pages.py stream_files: fromtimestamp(ts, tz=tz), not naive."""

    def test_aware_fromtimestamp_renders_stream_local(self):
        ts = BOUNDARY_INSTANT_UTC.timestamp()
        tz = parse_stream_timezone("America/New_York")
        aware = datetime.fromtimestamp(ts, tz=tz)
        assert aware.strftime("%Y-%m-%d %H:%M") == "2026-07-15 21:00"

    def test_aware_fromtimestamp_sydney(self):
        ts = BOUNDARY_INSTANT_UTC.timestamp()
        tz = parse_stream_timezone("+11:00")
        aware = datetime.fromtimestamp(ts, tz=tz)
        assert isinstance(tz, ZoneInfo)  # +11:00 maps to Australia/Sydney
        # AEST (+10:00) in July — winter in the southern hemisphere.
        assert aware.strftime("%Y-%m-%d %H:%M") == "2026-07-16 11:00"

    def test_utc_offset_is_stream_not_server(self):
        """The rendered wall-clock time must depend only on the stream tz,
        never on the server's local zone."""
        ts = BOUNDARY_INSTANT_UTC.timestamp()
        harvard = datetime.fromtimestamp(ts, tz=parse_stream_timezone("-05:00"))
        sydney = datetime.fromtimestamp(ts, tz=parse_stream_timezone("+11:00"))
        assert harvard == sydney  # same instant
        assert harvard.strftime("%H:%M") != sydney.strftime("%H:%M")
