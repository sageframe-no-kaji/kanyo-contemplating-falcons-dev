"""
Presence tracking: a reasoned judgment over richer evidence.

PresenceTracker sits between the detector and the state machine. The state
machine keeps consuming a boolean; this module decides that boolean from
three kinds of evidence instead of a per-frame recognition bit:

- ENTER (strict, unchanged semantics): while absent, presence begins only on
  a target-class detection at full confidence (the ``filtered_detections``
  argument). Nothing weaker — no raw box, no motion — starts a presence.
- SUSTAIN (permissive, the fix): while present, presence is sustained by a
  low-confidence detection of ANY class overlapping the presence region
  (Harvard's at-lens birds classify as "elephant" or "person" — those boxes
  are evidence the bird is still there), or by motion inside the region.
- PARKED (the core fix): while present, NO detection plus NO region motion is
  exactly the signature of a sleeping bird — the tracker stays present.
  Absence of recognition is no longer evidence of absence.

Exit requires positive evidence, one of:

- A motion burst in/leaving the region with no detection at any threshold,
  followed by quiet polls with neither region motion nor sustain-level
  detection. The tracker then reports absent; the state machine's existing
  exit timeout is the debounce from there, and any renewed evidence (sustain
  detection overlapping the region, or region motion) flips the tracker back
  to present.
- The failsafe: zero evidence of any kind — no detection at any threshold,
  no motion — for ``absence_failsafe_seconds``, so a missed departure can
  never hold presence forever.

Motion evidence is cheap frame differencing: grayscale, downscaled, absdiff
against the previous poll's frame, per-pixel threshold, changed-area
fraction. If the WHOLE frame changed beyond ``global_change_frac`` (IR/day
flip, exposure swing, camera adjustment), that poll's motion evidence is
discarded entirely — a global change must not read as bird motion or as a
departure — but the frame still becomes the new baseline.

The region is the last confirmed detection bbox expanded by
``region_margin_frac``, clamped to the frame. It follows the bird: filtered
detections re-seed it anywhere, sustain-level raw detections overlapping it
update it, and on motion-only evidence it shifts toward the motion centroid,
bounded per update so a noisy diff cannot teleport it across the frame.

The module is pure: no pipeline imports, no YOLO calls, no wall-clock reads.
All time comes from the ``timestamp`` argument, so tests are deterministic.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

import cv2
import numpy as np

from kanyo.detection.detect import Detection
from kanyo.utils.logger import get_logger

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = get_logger(__name__)

# Region as (x1, y1, x2, y2) in full-frame pixel coordinates.
Region = tuple[int, int, int, int]

# Downscale target for motion differencing (max width in pixels).
DOWNSCALE_MAX_WIDTH = 320

# On motion-only evidence, the region may shift by at most this fraction of
# its own width/height per update.
MAX_REGION_SHIFT_FRAC = 0.5


class PresenceTracker:
    """Evidence-based presence judgment feeding the state machine's boolean."""

    def __init__(
        self,
        sustain_confidence: float = 0.15,
        region_margin_frac: float = 0.25,
        motion_pixel_threshold: int = 25,
        motion_min_area_frac: float = 0.02,
        global_change_frac: float = 0.5,
        absence_failsafe_seconds: float = 3600.0,
    ) -> None:
        """
        Initialize the tracker.

        Args:
            sustain_confidence: Any-class confidence floor to sustain presence
                for a box overlapping the region.
            region_margin_frac: Region = last confirmed bbox expanded by this
                fraction of the bbox size on each side.
            motion_pixel_threshold: Grayscale absdiff threshold per pixel.
            motion_min_area_frac: Changed fraction of the region that counts
                as region motion.
            global_change_frac: Whole-frame changed fraction above which the
                poll's motion evidence is discarded (IR/day flip).
            absence_failsafe_seconds: Zero-evidence ceiling before forced
                absence.
        """
        self.sustain_confidence = sustain_confidence
        self.region_margin_frac = region_margin_frac
        self.motion_pixel_threshold = motion_pixel_threshold
        self.motion_min_area_frac = motion_min_area_frac
        self.global_change_frac = global_change_frac
        self.absence_failsafe_seconds = absence_failsafe_seconds

        self._present: bool = False
        self._reporting_absent: bool = False
        self._departure_candidate: bool = False
        self._region: Region | None = None
        self._prev_small: NDArray[np.uint8] | None = None
        self._last_evidence_time: datetime | None = None
        self._last_evidence_type: str | None = None

    def reset(self) -> None:
        """Clear all state, including the motion baseline."""
        self._go_absent()
        self._prev_small = None

    def update(
        self,
        frame: NDArray[np.uint8],
        timestamp: datetime,
        filtered_detections: list[Detection],
        raw_detections: list[Detection],
    ) -> bool:
        """Return the presence boolean for the state machine.

        Args:
            frame: BGR image as numpy array.
            timestamp: The frame's read-time timestamp (the time authority).
            filtered_detections: Target-class detections at full confidence
                (what ``detect_birds()`` returns today) — the ENTER evidence.
            raw_detections: Any-class detections down to the sustain floor —
                the SUSTAIN evidence.
        """
        frame_h, frame_w = frame.shape[:2]
        region_motion, motion_centroid = self._evaluate_motion(frame)

        if not self._present:
            if filtered_detections:
                best = max(filtered_detections, key=lambda d: d.confidence)
                self._present = True
                self._reporting_absent = False
                self._departure_candidate = False
                self._region = self._expand_bbox(best.bbox, frame_w, frame_h)
                self._record_evidence(timestamp, "detection")
                logger.debug(f"Presence ENTER: region={self._region}")
                return True
            # Enter stays strict: raw boxes and motion never start a presence.
            return False

        sustain_detections = [
            d
            for d in raw_detections
            if d.confidence >= self.sustain_confidence and self._overlaps_region(d.bbox)
        ]

        # Region maintenance: filtered detections re-seed anywhere; sustain
        # detections (already region-overlapping) update it; motion-only
        # evidence shifts it toward the motion centroid, bounded per update.
        if filtered_detections:
            best = max(filtered_detections, key=lambda d: d.confidence)
            self._region = self._expand_bbox(best.bbox, frame_w, frame_h)
        elif sustain_detections:
            best = max(sustain_detections, key=lambda d: d.confidence)
            self._region = self._expand_bbox(best.bbox, frame_w, frame_h)
        elif region_motion and motion_centroid is not None:
            self._shift_region_toward(motion_centroid, frame_w, frame_h)

        if filtered_detections or sustain_detections:
            # Detection at any threshold: solid evidence, clears any pending
            # departure and any absence report.
            self._record_evidence(timestamp, "detection")
            self._departure_candidate = False
            self._reporting_absent = False
            return True

        if region_motion:
            # Motion with no detection at any threshold: the bird may be
            # moving out. Evidence for now, departure candidate for later.
            self._record_evidence(timestamp, "region_motion")
            self._departure_candidate = True
            self._reporting_absent = False
            return True

        # No evidence at all this poll.

        # Failsafe: zero evidence of any kind for too long forces absence.
        if (
            self._last_evidence_time is not None
            and (timestamp - self._last_evidence_time).total_seconds()
            >= self.absence_failsafe_seconds
        ):
            logger.debug("Presence FAILSAFE: zero evidence ceiling reached, forcing absence")
            self._go_absent()
            return False

        if self._departure_candidate or self._reporting_absent:
            # Positive exit evidence: a motion burst followed by quiet. Report
            # absent; the state machine's exit timeout debounces from here.
            # Renewed evidence flips back to present above.
            if not self._reporting_absent:
                logger.debug("Presence EXIT candidate: motion burst then quiet, reporting absent")
            self._reporting_absent = True
            self._departure_candidate = False
            return False

        # The core fix: no detection AND no region motion is exactly what a
        # sleeping bird produces. Still present.
        return True

    def state_info(self) -> dict[str, Any]:
        """Diagnostics snapshot for logging."""
        return {
            "present": self._present and not self._reporting_absent,
            "episode_active": self._present,
            "reporting_absent": self._reporting_absent,
            "departure_candidate": self._departure_candidate,
            "region": self._region,
            "last_evidence_time": (
                self._last_evidence_time.isoformat() if self._last_evidence_time else None
            ),
            "last_evidence_type": self._last_evidence_type,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _go_absent(self) -> None:
        """Drop the presence episode (keeps the motion baseline)."""
        self._present = False
        self._reporting_absent = False
        self._departure_candidate = False
        self._region = None
        self._last_evidence_time = None
        self._last_evidence_type = None

    def _record_evidence(self, timestamp: datetime, evidence_type: str) -> None:
        self._last_evidence_time = timestamp
        self._last_evidence_type = evidence_type

    def _evaluate_motion(self, frame: NDArray[np.uint8]) -> tuple[bool, tuple[float, float] | None]:
        """Frame-difference motion evidence for this poll.

        Returns (region_motion, motion_centroid) where region_motion is True
        when the changed fraction inside the region meets
        ``motion_min_area_frac`` and the frame is not globally discounted;
        motion_centroid is the changed-pixel centroid inside the region in
        full-frame coordinates (None when there is no usable motion).

        Always stores the downscaled frame as the new baseline — including on
        a global-change discount.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frame_h, frame_w = gray.shape[:2]
        scale = min(1.0, DOWNSCALE_MAX_WIDTH / frame_w)
        if scale < 1.0:
            small_w = max(1, int(frame_w * scale))
            small_h = max(1, int(frame_h * scale))
            resized = cv2.resize(gray, (small_w, small_h), interpolation=cv2.INTER_AREA)
        else:
            resized = gray
        # cv2's stubs type image returns as a broad int/float ndarray; a uint8
        # BGR input keeps uint8 through cvtColor/resize, so narrow it here.
        small = cast("NDArray[np.uint8]", resized)

        prev = self._prev_small
        self._prev_small = small

        if prev is None or prev.shape != small.shape:
            return False, None

        diff = cv2.absdiff(small, prev)
        changed = diff > self.motion_pixel_threshold
        global_frac = float(changed.mean())

        if global_frac > self.global_change_frac:
            # IR/day flip, exposure swing, camera adjustment: discard this
            # poll's motion evidence entirely.
            logger.debug(
                f"Motion evidence discounted: global change {global_frac:.2f} > "
                f"{self.global_change_frac:.2f}"
            )
            return False, None

        if self._region is None:
            return False, None

        # Map the region into downscaled coordinates.
        small_h, small_w = small.shape[:2]
        sx = small_w / frame_w
        sy = small_h / frame_h
        x1, y1, x2, y2 = self._region
        rx1 = max(0, min(small_w, int(x1 * sx)))
        ry1 = max(0, min(small_h, int(y1 * sy)))
        rx2 = max(0, min(small_w, int(np.ceil(x2 * sx))))
        ry2 = max(0, min(small_h, int(np.ceil(y2 * sy))))
        if rx2 <= rx1 or ry2 <= ry1:
            return False, None

        region_changed = changed[ry1:ry2, rx1:rx2]
        region_frac = float(region_changed.mean())
        if region_frac < self.motion_min_area_frac:
            return False, None

        ys, xs = np.nonzero(region_changed)
        centroid_x = (float(xs.mean()) + rx1) / sx
        centroid_y = (float(ys.mean()) + ry1) / sy
        return True, (centroid_x, centroid_y)

    def _expand_bbox(self, bbox: tuple[int, int, int, int], frame_w: int, frame_h: int) -> Region:
        """Bbox plus margin (fraction of bbox size per side), clamped to frame."""
        x1, y1, x2, y2 = bbox
        mx = (x2 - x1) * self.region_margin_frac
        my = (y2 - y1) * self.region_margin_frac
        return (
            max(0, int(x1 - mx)),
            max(0, int(y1 - my)),
            min(frame_w, int(np.ceil(x2 + mx))),
            min(frame_h, int(np.ceil(y2 + my))),
        )

    def _overlaps_region(self, bbox: tuple[int, int, int, int]) -> bool:
        """True when bbox and the current region intersect with positive area."""
        if self._region is None:
            return False
        rx1, ry1, rx2, ry2 = self._region
        bx1, by1, bx2, by2 = bbox
        return bx1 < rx2 and bx2 > rx1 and by1 < ry2 and by2 > ry1

    def _shift_region_toward(
        self, centroid: tuple[float, float], frame_w: int, frame_h: int
    ) -> None:
        """Shift the region toward the motion centroid, bounded per update."""
        if self._region is None:
            return
        x1, y1, x2, y2 = self._region
        width = x2 - x1
        height = y2 - y1
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        max_dx = width * MAX_REGION_SHIFT_FRAC
        max_dy = height * MAX_REGION_SHIFT_FRAC
        dx = float(np.clip(centroid[0] - cx, -max_dx, max_dx))
        dy = float(np.clip(centroid[1] - cy, -max_dy, max_dy))
        nx1 = int(round(x1 + dx))
        ny1 = int(round(y1 + dy))
        # Clamp to frame bounds, preserving the region size where possible.
        nx1 = max(0, min(nx1, frame_w - width))
        ny1 = max(0, min(ny1, frame_h - height))
        self._region = (nx1, ny1, nx1 + width, ny1 + height)
