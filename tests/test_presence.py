"""Tests for the PresenceTracker module (024-B).

Synthetic numpy frames (dark background, bright blob) drive update() with
scripted detections and timestamps. No YOLO, no pipeline, no wall clock.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kanyo.detection.detect import Detection  # noqa: E402
from kanyo.detection.presence import PresenceTracker  # noqa: E402

FRAME_H = 240
FRAME_W = 320
T0 = datetime(2026, 7, 16, 12, 0, 0)


def ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_frame(blob: tuple[int, int, int, int] | None = None, value: int = 200) -> np.ndarray:
    """Dark frame with an optional bright rectangular blob (x1, y1, x2, y2)."""
    frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
    if blob is not None:
        x1, y1, x2, y2 = blob
        frame[y1:y2, x1:x2] = value
    return frame


def bird(confidence: float, bbox: tuple[int, int, int, int], when: datetime) -> Detection:
    return Detection(
        class_id=14, class_name="bird", confidence=confidence, bbox=bbox, timestamp=when
    )


def elephant(confidence: float, bbox: tuple[int, int, int, int], when: datetime) -> Detection:
    return Detection(
        class_id=20, class_name="elephant", confidence=confidence, bbox=bbox, timestamp=when
    )


def make_tracker(**overrides) -> PresenceTracker:
    params = dict(
        sustain_confidence=0.15,
        region_margin_frac=0.25,
        motion_pixel_threshold=25,
        motion_min_area_frac=0.02,
        global_change_frac=0.5,
        absence_failsafe_seconds=3600.0,
    )
    params.update(overrides)
    return PresenceTracker(**params)


def enter(tracker: PresenceTracker, blob: tuple[int, int, int, int], when: datetime) -> bool:
    """Drive a strict ENTER: filtered bird detection on a frame showing the blob."""
    detection = bird(0.8, blob, when)
    return tracker.update(make_frame(blob), when, [detection], [detection])


class TestMovingBlob:
    """Scenario 1: enter via filtered detection; motion + sustain keep presence;
    the region follows the blob."""

    def test_moving_blob_sustained_and_region_follows(self):
        tracker = make_tracker()
        blob0 = (100, 100, 140, 140)
        assert enter(tracker, blob0, ts(0)) is True
        region0 = tracker.state_info()["region"]

        # Blob moves right; only a low-confidence raw box sees it.
        blob1 = (120, 100, 160, 140)
        result = tracker.update(make_frame(blob1), ts(5), [], [bird(0.3, blob1, ts(5))])
        assert result is True

        blob2 = (140, 100, 180, 140)
        result = tracker.update(make_frame(blob2), ts(10), [], [bird(0.3, blob2, ts(10))])
        assert result is True

        region2 = tracker.state_info()["region"]
        assert region2 is not None
        # Region followed the blob to the right.
        assert region2[0] > region0[0]
        assert region2[2] > region0[2]
        info = tracker.state_info()
        assert info["present"] is True
        assert info["last_evidence_type"] == "detection"

    def test_motion_only_shifts_region_toward_blob(self):
        tracker = make_tracker()
        blob0 = (100, 100, 140, 140)
        assert enter(tracker, blob0, ts(0)) is True
        region0 = tracker.state_info()["region"]

        # Blob shifts within/near the region with NO detections at all:
        # motion evidence sustains presence and pulls the region along.
        blob1 = (115, 100, 155, 140)
        result = tracker.update(make_frame(blob1), ts(5), [], [])
        assert result is True
        info = tracker.state_info()
        assert info["last_evidence_type"] == "region_motion"
        region1 = info["region"]
        assert region1[0] >= region0[0]  # did not move away from the motion
        # Bounded shift: the region cannot teleport across the frame.
        assert abs(region1[0] - region0[0]) <= (region0[2] - region0[0])


class TestParkedBlob:
    """Scenario 2 (the core fix): detection dropout on a motionless bird
    must NOT end presence."""

    def test_parked_blob_detection_dropout_stays_present(self):
        tracker = make_tracker()
        blob = (100, 100, 140, 140)
        assert enter(tracker, blob, ts(0)) is True

        frame = make_frame(blob)
        # YOLO stops seeing the bird entirely: no filtered, no raw, no motion.
        for t in (5, 10, 30, 60, 120, 600):
            assert tracker.update(frame, ts(t), [], []) is True, f"lost presence at t={t}"

        assert tracker.state_info()["present"] is True


class TestBlobExitsFrame:
    """Scenario 3: motion burst through/out of the region, then quiet with no
    detections, allows the departure path."""

    def test_exit_burst_then_quiet_reports_absent(self):
        tracker = make_tracker()
        blob0 = (100, 100, 140, 140)
        assert enter(tracker, blob0, ts(0)) is True

        # Motion burst: blob moves with no detection at any threshold.
        blob1 = (140, 100, 180, 140)
        assert tracker.update(make_frame(blob1), ts(5), [], []) is True

        # Blob gone; the disappearance itself may still register as motion.
        empty = make_frame(None)
        tracker.update(empty, ts(10), [], [])

        # Quiet frames, no detections: the tracker must report absent.
        assert tracker.update(empty, ts(15), [], []) is False
        assert tracker.update(empty, ts(20), [], []) is False
        assert tracker.state_info()["present"] is False

    def test_renewed_evidence_flips_back_to_present(self):
        tracker = make_tracker()
        blob0 = (100, 100, 140, 140)
        assert enter(tracker, blob0, ts(0)) is True
        region = tracker.state_info()["region"]

        # Burst then quiet: reporting absent.
        blob1 = (140, 100, 180, 140)
        tracker.update(make_frame(blob1), ts(5), [], [])
        empty = make_frame(None)
        tracker.update(empty, ts(10), [], [])
        assert tracker.update(empty, ts(15), [], []) is False

        # Renewed sustain-level evidence overlapping the region flips back.
        info = tracker.state_info()
        rx1, ry1, rx2, ry2 = info["region"]
        overlap_box = (rx1 + 1, ry1 + 1, rx1 + 20, ry1 + 20)
        result = tracker.update(empty, ts(20), [], [elephant(0.3, overlap_box, ts(20))])
        assert result is True
        assert tracker.state_info()["present"] is True
        assert region is not None  # sanity


class TestGlobalFlip:
    """Scenario 4: a whole-frame change (IR/day flip) is discounted — no
    motion evidence, no state change from that frame."""

    def test_global_flip_discounted_presence_unchanged(self):
        tracker = make_tracker()
        blob = (100, 100, 140, 140)
        assert enter(tracker, blob, ts(0)) is True

        frame = make_frame(blob)
        assert tracker.update(frame, ts(5), [], []) is True  # parked

        # IR/day flip: invert the whole frame in one step.
        flipped = (255 - frame).astype(np.uint8)
        assert tracker.update(flipped, ts(10), [], []) is True
        info = tracker.state_info()
        # The flip produced no motion evidence and no departure candidate.
        assert info["departure_candidate"] is False
        assert info["last_evidence_type"] == "detection"  # still the enter evidence

        # The flipped frame became the new baseline: the next identical frame
        # is quiet and presence holds (no burst was recorded).
        assert tracker.update(flipped, ts(15), [], []) is True
        assert tracker.state_info()["present"] is True


class TestFailsafe:
    """Scenario 5: zero evidence past absence_failsafe_seconds forces absence."""

    def test_failsafe_expiry_forces_absence(self):
        tracker = make_tracker(absence_failsafe_seconds=3600.0)
        blob = (100, 100, 140, 140)
        assert enter(tracker, blob, ts(0)) is True

        frame = make_frame(blob)
        # Zero evidence, but under the ceiling: still present.
        assert tracker.update(frame, ts(1000), [], []) is True
        assert tracker.update(frame, ts(3599), [], []) is True
        # Past the ceiling: forced absent.
        assert tracker.update(frame, ts(3605), [], []) is False
        assert tracker.state_info()["present"] is False
        assert tracker.state_info()["episode_active"] is False

    def test_evidence_resets_failsafe_clock(self):
        tracker = make_tracker(absence_failsafe_seconds=3600.0)
        blob = (100, 100, 140, 140)
        assert enter(tracker, blob, ts(0)) is True

        frame = make_frame(blob)
        # Sustain evidence at t=3000 resets the zero-evidence clock.
        det = elephant(0.3, blob, ts(3000))
        assert tracker.update(frame, ts(3000), [], [det]) is True
        # t=3605 is only 605s after the last evidence: still present.
        assert tracker.update(frame, ts(3605), [], []) is True
        # But the ceiling still lands, measured from the last evidence.
        assert tracker.update(frame, ts(6700), [], []) is False


class TestSustainByMisclassification:
    """Scenario 6 (Harvard elephant/person case): an any-class low-confidence
    box overlapping the region sustains presence."""

    def test_elephant_box_sustains_presence(self):
        tracker = make_tracker()
        blob = (100, 100, 140, 140)
        assert enter(tracker, blob, ts(0)) is True

        frame = make_frame(blob)
        det = elephant(0.2, (105, 105, 135, 135), ts(5))
        assert tracker.update(frame, ts(5), [], [det]) is True

        info = tracker.state_info()
        assert info["last_evidence_type"] == "detection"
        assert info["last_evidence_time"] == ts(5).isoformat()

    def test_sustain_below_floor_or_outside_region_is_not_evidence(self):
        tracker = make_tracker()
        blob = (100, 100, 140, 140)
        assert enter(tracker, blob, ts(0)) is True

        frame = make_frame(blob)
        # Below the sustain floor: not evidence (but parked logic still holds).
        weak = elephant(0.1, (105, 105, 135, 135), ts(5))
        assert tracker.update(frame, ts(5), [], [weak]) is True
        assert tracker.state_info()["last_evidence_time"] == ts(0).isoformat()

        # Outside the region: not evidence either.
        far = elephant(0.4, (250, 200, 300, 235), ts(10))
        assert tracker.update(frame, ts(10), [], [far]) is True
        assert tracker.state_info()["last_evidence_time"] == ts(0).isoformat()


class TestEnterStrictness:
    """Scenario 7: while absent, raw-only boxes and motion never start a
    presence."""

    def test_raw_boxes_and_motion_do_not_enter(self):
        tracker = make_tracker()
        empty = make_frame(None)
        assert tracker.update(empty, ts(0), [], []) is False

        # A blob appears (motion) with a high-confidence NON-target raw box.
        blob = (100, 100, 140, 140)
        raw = [elephant(0.9, blob, ts(5))]
        assert tracker.update(make_frame(blob), ts(5), [], raw) is False

        # The blob moves (more motion), with a low-confidence bird raw box.
        blob2 = (120, 100, 160, 140)
        raw2 = [bird(0.3, blob2, ts(10))]
        assert tracker.update(make_frame(blob2), ts(10), [], raw2) is False

        info = tracker.state_info()
        assert info["present"] is False
        assert info["episode_active"] is False
        assert info["region"] is None

    def test_filtered_detection_enters(self):
        tracker = make_tracker()
        blob = (100, 100, 140, 140)
        assert enter(tracker, blob, ts(0)) is True
        assert tracker.state_info()["present"] is True


class TestRegionMaintenance:
    """Filtered detections re-seed the region anywhere; wide frames are
    downscaled for motion differencing."""

    def test_filtered_detection_reseeds_region_while_present(self):
        tracker = make_tracker()
        blob0 = (100, 100, 140, 140)
        assert enter(tracker, blob0, ts(0)) is True

        # A full-confidence detection far from the current region re-seeds it.
        blob1 = (200, 150, 240, 190)
        det = bird(0.9, blob1, ts(5))
        assert tracker.update(make_frame(blob1), ts(5), [det], [det]) is True
        region = tracker.state_info()["region"]
        assert region[0] >= 180  # region now around the new bbox
        assert region[2] <= 260

    def test_wide_frame_downscaled_motion_still_detected(self):
        tracker = make_tracker()
        h, w = 480, 640  # wider than the 320px downscale target

        def wide_frame(blob):
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            if blob is not None:
                x1, y1, x2, y2 = blob
                frame[y1:y2, x1:x2] = 200
            return frame

        blob0 = (200, 200, 280, 280)
        det = bird(0.8, blob0, ts(0))
        assert tracker.update(wide_frame(blob0), ts(0), [det], [det]) is True

        # Motion-only sustain on the downscaled diff path.
        blob1 = (230, 200, 310, 280)
        assert tracker.update(wide_frame(blob1), ts(5), [], []) is True
        assert tracker.state_info()["last_evidence_type"] == "region_motion"


class TestReset:
    """reset() clears the episode and the motion baseline."""

    def test_reset_clears_state(self):
        tracker = make_tracker()
        blob = (100, 100, 140, 140)
        assert enter(tracker, blob, ts(0)) is True

        tracker.reset()
        info = tracker.state_info()
        assert info["present"] is False
        assert info["episode_active"] is False
        assert info["region"] is None
        assert info["last_evidence_time"] is None

        # Absent again: enter stays strict.
        assert tracker.update(make_frame(blob), ts(5), [], []) is False


class TestRegionGuards:
    """Degenerate region mapping and defensive guards on region helpers."""

    def test_resolution_drop_maps_region_off_frame_keeps_parked_presence(self):
        """A stream resolution drop can leave the region entirely outside the
        new frame. The mapped region is degenerate — no motion evidence can be
        read from it — and parked logic keeps presence."""
        tracker = make_tracker()

        # Enter on a 640x480 frame with the bird on the far right.
        h, w = 480, 640
        blob = (500, 200, 580, 280)
        frame0 = np.zeros((h, w, 3), dtype=np.uint8)
        frame0[200:280, 500:580] = 200
        det = bird(0.8, blob, ts(0))
        assert tracker.update(frame0, ts(0), [det], [det]) is True

        # Stream drops to 320x240: same downscaled shape as the 640x480
        # baseline, but the region (x ~ 480..600) is beyond the new width.
        small = np.zeros((240, 320, 3), dtype=np.uint8)
        assert tracker.update(small, ts(5), [], []) is True

        info = tracker.state_info()
        assert info["present"] is True
        # No motion evidence was recorded from the degenerate region.
        assert info["last_evidence_type"] == "detection"
        assert info["last_evidence_time"] == ts(0).isoformat()

    def test_overlap_guard_without_region_is_false(self):
        """No region (absent): no bbox can overlap it."""
        tracker = make_tracker()
        assert tracker._overlaps_region((0, 0, 10, 10)) is False

    def test_shift_guard_without_region_is_noop(self):
        """Shifting toward a centroid with no region is a safe no-op."""
        tracker = make_tracker()
        tracker._shift_region_toward((50.0, 50.0), FRAME_W, FRAME_H)
        assert tracker.state_info()["region"] is None
