"""Tests for the EventSignificanceFilter (ho-09 / 025-A).

Scripted event sequences with explicit timestamps drive the filter through
time deterministically — no wall-clock reads anywhere in the module.
"""

from datetime import datetime, timedelta

from kanyo.detection.event_types import FalconEvent
from kanyo.detection.significance_filter import EventSignificanceFilter, FilterDecision

T0 = datetime(2026, 7, 16, 12, 0, 0)


def ts(seconds: float) -> datetime:
    """Timestamp ``seconds`` after T0."""
    return T0 + timedelta(seconds=seconds)


def departed_metadata(start: datetime, end: datetime) -> dict:
    """DEPARTED metadata as the state machine builds it."""
    duration = (end - start).total_seconds()
    return {
        "visit_start": start,
        "visit_end": end,
        "visit_duration_seconds": duration,
        "total_visit_duration": duration,
    }


def depart(
    filt: EventSignificanceFilter,
    start: datetime,
    end: datetime,
    now: datetime,
) -> list[FilterDecision]:
    """Feed a DEPARTED for the visit [start, end] through the filter."""
    return filt.process((FalconEvent.DEPARTED, end, departed_metadata(start, end)), now)


def arrive(
    filt: EventSignificanceFilter, when: datetime, now: datetime | None = None
) -> list[FilterDecision]:
    """Feed an ARRIVED at ``when`` through the filter."""
    return filt.process((FalconEvent.ARRIVED, when, {"visit_start": when}), now or when)


class TestMergeWindow:
    def test_departed_then_rearrival_merges_into_one_row(self):
        """DEPARTED then ARRIVED at +120s: both swallowed; the eventual real
        departure releases ONE row spanning first arrival to last visit_end,
        merged_segments == 2, one notification."""
        filt = EventSignificanceFilter(merge_window_seconds=300)

        # First visit: T0 → T0+600. DEPARTED processed at its event time.
        decisions = depart(filt, ts(0), ts(600), now=ts(600))
        assert decisions == []  # held

        # Re-arrival 120s later: swallowed.
        decisions = arrive(filt, ts(720))
        assert len(decisions) == 1
        swallowed = decisions[0]
        assert swallowed.notify is False
        assert swallowed.record is False
        assert swallowed.discard_arrival_clip is True
        assert swallowed.merged_segments == 2

        # Second segment departs at +900s; held with merged metadata.
        decisions = depart(filt, ts(720), ts(900), now=ts(900))
        assert decisions == []

        # Window expires: one merged release.
        decisions = filt.tick(ts(900 + 301))
        assert len(decisions) == 1
        released = decisions[0]
        assert released.event_type == FalconEvent.DEPARTED
        assert released.notify is True
        assert released.record is True
        assert released.merged_segments == 2
        assert released.metadata["visit_start"] == ts(0)
        assert released.metadata["visit_end"] == ts(900)
        assert released.metadata["visit_duration_seconds"] == 900.0
        assert released.metadata["total_visit_duration"] == 900.0

    def test_departed_with_no_rearrival_released_at_expiry(self):
        """A lone DEPARTED is released via tick at window expiry with its
        metadata unchanged."""
        filt = EventSignificanceFilter(merge_window_seconds=300)
        metadata = departed_metadata(ts(0), ts(600))

        assert filt.process((FalconEvent.DEPARTED, ts(600), metadata), ts(600)) == []
        assert filt.tick(ts(600 + 300)) == []  # boundary: not yet expired

        decisions = filt.tick(ts(600 + 301))
        assert len(decisions) == 1
        released = decisions[0]
        assert released.event_type == FalconEvent.DEPARTED
        assert released.event_time == ts(600)
        assert released.notify is True
        assert released.record is True
        assert released.merged_segments == 1
        assert released.insignificant is False
        assert released.metadata == metadata

    def test_three_segment_chain(self):
        """Two swallowed pairs produce merged_segments == 3."""
        filt = EventSignificanceFilter(merge_window_seconds=300)

        assert depart(filt, ts(0), ts(600), now=ts(600)) == []
        assert arrive(filt, ts(700))[0].merged_segments == 2
        assert depart(filt, ts(700), ts(1300), now=ts(1300)) == []
        assert arrive(filt, ts(1400))[0].merged_segments == 3
        assert depart(filt, ts(1400), ts(2000), now=ts(2000)) == []

        decisions = filt.tick(ts(2000 + 301))
        assert len(decisions) == 1
        released = decisions[0]
        assert released.merged_segments == 3
        assert released.metadata["visit_start"] == ts(0)
        assert released.metadata["visit_end"] == ts(2000)

    def test_arrival_after_window_releases_hold_and_passes_through(self):
        """An ARRIVED past the window releases the held DEPARTED and passes
        through itself — two decisions from one event."""
        filt = EventSignificanceFilter(merge_window_seconds=300)

        assert depart(filt, ts(0), ts(600), now=ts(600)) == []
        decisions = arrive(filt, ts(600 + 400))

        assert len(decisions) == 2
        released, passed = decisions
        assert released.event_type == FalconEvent.DEPARTED
        assert released.notify is True
        assert released.record is True
        assert released.merged_segments == 1
        assert passed.event_type == FalconEvent.ARRIVED
        assert passed.notify is True
        assert passed.discard_arrival_clip is False

    def test_merge_window_zero_disables_merging(self):
        """merge_window_seconds=0: DEPARTED released immediately in process."""
        filt = EventSignificanceFilter(merge_window_seconds=0)

        decisions = depart(filt, ts(0), ts(600), now=ts(600))
        assert len(decisions) == 1
        assert decisions[0].event_type == FalconEvent.DEPARTED
        assert decisions[0].notify is True
        assert decisions[0].record is True

    def test_double_departed_releases_stale_hold_defensively(self):
        """The state machine cannot fire DEPARTED twice without an ARRIVED,
        but a stale hold is released rather than silently overwritten."""
        filt = EventSignificanceFilter(merge_window_seconds=300)

        assert depart(filt, ts(0), ts(600), now=ts(600)) == []
        decisions = depart(filt, ts(700), ts(900), now=ts(900))

        assert len(decisions) == 1  # the stale hold, released
        assert decisions[0].event_type == FalconEvent.DEPARTED
        assert decisions[0].metadata["visit_end"] == ts(600)
        # The new DEPARTED is now the held one.
        assert filt.state_info()["held_departure"] == ts(900).isoformat()

    def test_duration_falls_back_to_metadata_seconds(self):
        """Without datetime span fields, visit_duration_seconds decides
        significance."""
        filt = EventSignificanceFilter(merge_window_seconds=0, min_significant_seconds=30)

        decisions = filt.process(
            (FalconEvent.DEPARTED, ts(600), {"visit_duration_seconds": 20.0}), ts(600)
        )
        assert decisions[0].insignificant is True

    def test_hold_already_expired_at_process_time_released_immediately(self):
        """DEPARTED whose event_time already trails ``now`` past the window
        (long exit_timeout) is released in the same process call."""
        filt = EventSignificanceFilter(merge_window_seconds=60)

        # event_time (visit_end) is 90s before now — beyond the 60s window.
        decisions = depart(filt, ts(0), ts(600), now=ts(690))
        assert len(decisions) == 1
        assert decisions[0].event_type == FalconEvent.DEPARTED


class TestMinimumSignificance:
    def test_short_visit_recorded_log_only(self):
        """A 20-second visit is released insignificant: no notification,
        row still recorded."""
        filt = EventSignificanceFilter(min_significant_seconds=30)

        assert depart(filt, ts(0), ts(20), now=ts(20)) == []
        decisions = filt.tick(ts(20 + 301))

        assert len(decisions) == 1
        released = decisions[0]
        assert released.insignificant is True
        assert released.notify is False
        assert released.record is True

    def test_merged_visit_still_under_threshold_is_insignificant(self):
        """Merge + insignificance interaction: a merged visit whose total
        detection-duration is still under threshold stays insignificant."""
        filt = EventSignificanceFilter(merge_window_seconds=300, min_significant_seconds=30)

        # 10s visit, re-arrival, 5s second segment: merged span T0 → T0+25.
        assert depart(filt, ts(0), ts(10), now=ts(10)) == []
        assert arrive(filt, ts(20))[0].discard_arrival_clip is True
        assert depart(filt, ts(20), ts(25), now=ts(25)) == []

        decisions = filt.tick(ts(25 + 301))
        assert len(decisions) == 1
        released = decisions[0]
        assert released.merged_segments == 2
        assert released.insignificant is True
        assert released.notify is False
        assert released.record is True
        assert released.metadata["visit_duration_seconds"] == 25.0

    def test_merged_visit_over_threshold_is_significant(self):
        """The merged span, not the last segment, decides significance."""
        filt = EventSignificanceFilter(merge_window_seconds=300, min_significant_seconds=30)

        # 20s segment + 10s segment, but the merged span is 50s.
        assert depart(filt, ts(0), ts(20), now=ts(20)) == []
        arrive(filt, ts(40))
        assert depart(filt, ts(40), ts(50), now=ts(50)) == []

        released = filt.tick(ts(50 + 301))[0]
        assert released.insignificant is False
        assert released.notify is True

    def test_threshold_zero_disables_significance_gate(self):
        filt = EventSignificanceFilter(merge_window_seconds=0, min_significant_seconds=0)

        decisions = depart(filt, ts(0), ts(5), now=ts(5))
        assert decisions[0].insignificant is False
        assert decisions[0].notify is True


class TestActivityDamping:
    def _arrive_n(self, filt: EventSignificanceFilter, count: int, spacing: float = 60):
        """Drive ``count`` pass-through arrivals ``spacing`` seconds apart.
        Returns the list of per-arrival decisions."""
        decisions = []
        for i in range(count):
            when = ts(i * spacing)
            result = arrive(filt, when)
            assert len(result) == 1
            decisions.append(result[0])
        return decisions

    def test_ninth_arrival_enters_damped_mode(self):
        """Nine arrivals in an hour with threshold 8: the ninth is damped;
        tick yields a summary; a rate drop restores notifications."""
        filt = EventSignificanceFilter(damping_arrivals_threshold=8, damping_window_hours=1)

        decisions = self._arrive_n(filt, 9, spacing=60)
        assert all(d.notify for d in decisions[:8])
        assert decisions[8].notify is False

        # Tick emits exactly one summary per window.
        summaries = filt.tick(ts(9 * 60))
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.is_summary is True
        assert summary.event_type is None
        assert summary.notify is True
        assert summary.record is False
        assert summary.metadata["count"] == 9
        assert filt.tick(ts(9 * 60 + 30)) == []  # no second summary yet

        # Two hours later the window is empty: notifications resume.
        late = arrive(filt, ts(2 * 3600))
        assert late[0].notify is True

    def test_tick_alone_exits_damped_mode(self):
        """Damped mode exits via tick when arrivals age out of the window."""
        filt = EventSignificanceFilter(damping_arrivals_threshold=2, damping_window_hours=1)
        self._arrive_n(filt, 3, spacing=10)
        assert filt.state_info()["damped"] is True

        filt.tick(ts(2 * 3600))
        assert filt.state_info()["damped"] is False

    def test_released_durations_age_out_of_the_summary_window(self):
        """Old released visits are pruned from the summary median data."""
        filt = EventSignificanceFilter(
            merge_window_seconds=0,
            min_significant_seconds=0,
            damping_arrivals_threshold=2,
            damping_window_hours=1,
        )
        # One visit released early, well before the damped stretch.
        arrive(filt, ts(0))
        depart(filt, ts(0), ts(100), now=ts(100))

        # Three hours later, a burst enters damped mode with fresh visits.
        base = 3 * 3600
        for i in range(3):
            start = ts(base + i * 300)
            arrive(filt, start)
            depart(filt, start, start + timedelta(seconds=40), now=ts(base + i * 300 + 50))

        summary = filt.tick(ts(base + 1000))[0]
        assert summary.metadata["count"] == 3  # the early arrival aged out
        assert summary.metadata["median_duration_seconds"] == 40.0

    def test_summary_median_from_released_durations(self):
        """The summary median comes from visits released within the window."""
        filt = EventSignificanceFilter(
            merge_window_seconds=0,
            min_significant_seconds=0,
            damping_arrivals_threshold=2,
            damping_window_hours=1,
        )

        # Three arrivals (damped after the third) with released visits of
        # 10s, 20s, and 40s → median 20.
        for i, duration in enumerate((10, 20, 40)):
            start = ts(i * 300)
            arrive(filt, start)
            depart(filt, start, start + timedelta(seconds=duration), now=ts(i * 300 + 60))

        summaries = filt.tick(ts(1000))
        assert len(summaries) == 1
        assert summaries[0].metadata["median_duration_seconds"] == 20.0

    def test_damped_departure_release_suppresses_notification(self):
        """While damped, a released departure records its row but does not
        notify individually."""
        filt = EventSignificanceFilter(
            merge_window_seconds=60,
            min_significant_seconds=0,
            damping_arrivals_threshold=2,
            damping_window_hours=1,
        )
        self._arrive_n(filt, 3, spacing=10)  # damped

        assert depart(filt, ts(30), ts(400), now=ts(400)) == []
        released = filt.tick(ts(400 + 61))[0]
        assert released.event_type == FalconEvent.DEPARTED
        assert released.notify is False
        assert released.record is True
        assert released.insignificant is False

    def test_threshold_zero_disables_damping(self):
        filt = EventSignificanceFilter(damping_arrivals_threshold=0)
        decisions = self._arrive_n(filt, 20, spacing=10)
        assert all(d.notify for d in decisions)
        assert filt.tick(ts(300)) == []


class TestDisabled:
    def test_every_event_passes_through_immediately(self):
        """enabled=False: pass-through with notify=True, record=True, no
        holds — today's behavior."""
        filt = EventSignificanceFilter(enabled=False)

        for event_type, event_time, metadata in (
            (FalconEvent.ARRIVED, ts(0), {"visit_start": ts(0)}),
            (FalconEvent.ROOSTING, ts(1800), {"visit_duration_seconds": 1800}),
            (FalconEvent.DEPARTED, ts(20), departed_metadata(ts(0), ts(20))),
        ):
            decisions = filt.process((event_type, event_time, metadata), event_time)
            assert len(decisions) == 1
            decision = decisions[0]
            assert decision.event_type == event_type
            assert decision.event_time == event_time
            assert decision.metadata == metadata
            assert decision.notify is True
            assert decision.record is True
            assert decision.merged_segments == 1
            assert decision.insignificant is False
            assert decision.discard_arrival_clip is False

        assert filt.state_info()["held_departure"] is None
        assert filt.tick(ts(10_000)) == []

    def test_disabled_short_visit_not_flagged(self):
        """A 5-second visit passes through unflagged when disabled."""
        filt = EventSignificanceFilter(enabled=False, min_significant_seconds=30)
        decisions = depart(filt, ts(0), ts(5), now=ts(5))
        assert decisions[0].insignificant is False
        assert decisions[0].notify is True


class TestRoostingPassThrough:
    def test_roosting_untouched(self):
        """ROOSTING passes through with notify=True, record=False — it has
        no event-store row today."""
        filt = EventSignificanceFilter()
        decisions = filt.process(
            (FalconEvent.ROOSTING, ts(1800), {"visit_duration_seconds": 1800}), ts(1800)
        )
        assert len(decisions) == 1
        assert decisions[0].notify is True
        assert decisions[0].record is False

    def test_roosting_passes_through_while_departure_held(self):
        """A ROOSTING inside a continuation chain does not disturb the hold."""
        filt = EventSignificanceFilter(merge_window_seconds=300)
        assert depart(filt, ts(0), ts(600), now=ts(600)) == []
        arrive(filt, ts(700))  # swallowed — chain open, visit continues

        decisions = filt.process((FalconEvent.ROOSTING, ts(2500), {}), ts(2500))
        assert len(decisions) == 1
        assert decisions[0].event_type == FalconEvent.ROOSTING


class TestFlush:
    def test_flush_releases_held_departure_immediately(self):
        """Shutdown flush releases the held row regardless of the window."""
        filt = EventSignificanceFilter(merge_window_seconds=300)
        assert depart(filt, ts(0), ts(600), now=ts(600)) == []

        decisions = filt.flush(ts(610))
        assert len(decisions) == 1
        assert decisions[0].event_type == FalconEvent.DEPARTED
        assert decisions[0].record is True

        assert filt.flush(ts(620)) == []  # nothing left to flush

    def test_flush_with_nothing_held(self):
        assert EventSignificanceFilter().flush(ts(0)) == []


class TestStateInfo:
    def test_state_info_reflects_hold_and_chain(self):
        filt = EventSignificanceFilter(merge_window_seconds=300)
        info = filt.state_info()
        assert info["enabled"] is True
        assert info["held_departure"] is None
        assert info["chain_segments"] == 1
        assert info["damped"] is False

        depart(filt, ts(0), ts(600), now=ts(600))
        info = filt.state_info()
        assert info["held_departure"] == ts(600).isoformat()

        arrive(filt, ts(700))
        info = filt.state_info()
        assert info["held_departure"] is None
        assert info["chain_segments"] == 2
        assert info["chain_visit_start"] == ts(0).isoformat()
