"""
Falcon detection using YOLOv8.

Provides FalconDetector class for detecting birds/falcons in frames.
Handles model loading, inference, and result formatting with timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from kanyo.utils.logger import get_logger

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = get_logger(__name__)

# COCO class IDs - animals that might be detected as "falcon"
# On a dedicated falcon cam, any animal detection = falcon present
BIRD_CLASS_ID = 14
ANIMAL_CLASS_IDS = {
    14: "bird",
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
    20: "elephant",
    21: "bear",
    22: "zebra",
    23: "giraffe",
}


def is_ir_mode(frame) -> bool:
    """Detect if frame is infrared/night vision (grayscale).

    IR frames have R ≈ G ≈ B for all pixels since they're essentially grayscale.
    """
    g, r = frame[:, :, 1], frame[:, :, 2]
    diff = np.abs(r.astype(int) - g.astype(int)).mean()
    return diff < 5


@dataclass
class Detection:
    """A single object detection result."""

    class_id: int
    class_name: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    timestamp: datetime

    def to_dict(self) -> dict:
        """Serialize for JSON."""
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 3),
            "bbox": self.bbox,
            "timestamp": self.timestamp.isoformat(),
        }


class FalconDetector:
    """
    YOLOv8-based falcon/bird detector.

    Usage:
        detector = FalconDetector()
        detections = detector.detect(frame)
        if detector.has_bird(detections):
            print("Bird detected!")
    """

    def __init__(
        self,
        model_path: str | Path = "models/yolov8n.pt",
        confidence_threshold: float = 0.5,
        confidence_threshold_ir: float | None = None,
        target_classes: list[int] | None = None,
        detect_any_animal: bool = True,
        animal_classes: list[int] | None = None,
        raw_floor_confidence: float | None = None,
    ):
        """
        Initialize detector.

        Args:
            model_path: Path to YOLO model (auto-downloads if missing)
            confidence_threshold: Minimum confidence for detections
            confidence_threshold_ir: Optional lower threshold for IR/night mode
            target_classes: Class IDs to detect (default: [14] for birds)
            detect_any_animal: If True, detect any animal (for falcon cams where
                               the model may misclassify falcons as cats/dogs)
            animal_classes: COCO class IDs to treat as "animal" when detect_any_animal=True
            raw_floor_confidence: Optional low floor for the raw-detections view
                (presence layer). When set, the single model call runs at
                min(raw_floor_confidence, effective threshold) and the existing
                filtered semantics are reproduced by post-filtering in code.
                When None, behavior is exactly the historical single-threshold
                call.
        """
        self.model_path = Path(model_path)
        self.confidence_threshold = confidence_threshold
        self.confidence_threshold_ir = confidence_threshold_ir
        self.detect_any_animal = detect_any_animal
        self.raw_floor_confidence = raw_floor_confidence

        # Use provided animal_classes or fall back to ANIMAL_CLASS_IDS keys
        default_animals = list(ANIMAL_CLASS_IDS.keys())

        if detect_any_animal:
            self.target_classes = animal_classes or default_animals
        else:
            self.target_classes = target_classes or [BIRD_CLASS_ID]
        self._model = None

    @property
    def model(self):
        """Lazy-load the YOLO model."""
        if self._model is None:
            from ultralytics import YOLO

            logger.info(f"Loading YOLO model from {self.model_path}...")
            self._model = YOLO(str(self.model_path))
            logger.info("Model loaded successfully")
        return self._model

    def _run_inference(
        self,
        frame: NDArray[np.uint8],
        timestamp: datetime,
    ) -> tuple[list[Detection], list[Detection]]:
        """
        Run ONE YOLO inference and return two post-filtered views.

        Returns:
            (filtered, raw) where:
            - filtered: target classes only, confidence >= effective threshold
              (respecting the IR-mode switch) — byte-identical semantics to the
              historical single-threshold model call.
            - raw: every box of every class with confidence >=
              raw_floor_confidence (falls back to the effective threshold when
              the floor is unset). Presence-layer evidence: at-lens birds that
              classify as "elephant" or "person" live here.

        The model is invoked exactly once per call. When raw_floor_confidence
        is set, the call runs at min(raw_floor_confidence, effective threshold)
        and the filtered view is reconstructed in code.
        """
        # Determine effective threshold based on IR mode
        ir_mode = is_ir_mode(frame)
        if ir_mode and self.confidence_threshold_ir is not None:
            effective_threshold = self.confidence_threshold_ir
        else:
            effective_threshold = self.confidence_threshold

        if self.raw_floor_confidence is not None:
            model_conf = min(self.raw_floor_confidence, effective_threshold)
            raw_floor = self.raw_floor_confidence
        else:
            model_conf = effective_threshold
            raw_floor = effective_threshold

        results = self.model(
            frame,
            conf=model_conf,
            verbose=False,
        )

        filtered: list[Detection] = []
        raw: list[Detection] = []
        total_checked = 0
        all_detections_debug = []

        for result in results:
            for box in result.boxes:
                total_checked += 1
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                class_name = result.names[class_id]

                # Debug: log ALL detections
                all_detections_debug.append(f"{class_name}({class_id}):{confidence:.2f}")

                bbox_list = list(map(int, box.xyxy[0].tolist()))
                bbox: tuple[int, int, int, int] = (
                    bbox_list[0],
                    bbox_list[1],
                    bbox_list[2],
                    bbox_list[3],
                )
                detection = Detection(
                    class_id=class_id,
                    class_name=class_name,
                    confidence=confidence,
                    bbox=bbox,
                    timestamp=timestamp,
                )

                if confidence >= raw_floor:
                    raw.append(detection)

                # Filtered view: target classes at the effective threshold —
                # the pre-raw-floor semantics, preserved for existing callers.
                if class_id in self.target_classes and confidence >= effective_threshold:
                    filtered.append(detection)

        # Debug logging
        if all_detections_debug:
            logger.debug(
                f"YOLO found {total_checked} objects: {', '.join(all_detections_debug[:5])}"
            )

        if filtered:
            max_confidence = max(d.confidence for d in filtered)
            mode_str = "IR" if ir_mode else "DAY"
            logger.debug(
                f"[{mode_str}] Falcon detected: confidence={max_confidence:.3f} "
                f"(threshold={effective_threshold:.2f})"
            )
        else:
            mode_str = "IR" if ir_mode else "DAY"
            logger.debug(
                f"[{mode_str}] No falcon detected "
                f"(checked {total_checked} detections, "
                f"threshold={effective_threshold:.2f})"
            )

        return filtered, raw

    def detect(
        self,
        frame: NDArray[np.uint8],
        timestamp: datetime | None = None,
    ) -> list[Detection]:
        """
        Run detection on a frame.

        Args:
            frame: BGR image as numpy array
            timestamp: When this frame was captured (default: now)

        Returns:
            List of Detection objects
        """
        timestamp = timestamp or datetime.now()
        filtered, _ = self._run_inference(frame, timestamp)
        return filtered

    def detect_with_raw(
        self,
        frame: NDArray[np.uint8],
        timestamp: datetime | None = None,
    ) -> tuple[list[Detection], list[Detection]]:
        """
        Run ONE inference and return (filtered, raw) detections.

        filtered is exactly what detect_birds() returns today; raw is every
        box of every class at or above raw_floor_confidence (or the effective
        threshold when the floor is unset). See _run_inference for details.

        Args:
            frame: BGR image as numpy array
            timestamp: When this frame was captured (default: now)

        Returns:
            Tuple of (filtered, raw) Detection lists from a single inference.
        """
        timestamp = timestamp or datetime.now()
        return self._run_inference(frame, timestamp)

    def detect_birds(
        self,
        frame: NDArray[np.uint8],
        timestamp: datetime | None = None,
    ) -> list[Detection]:
        """Detect only birds/falcons in frame."""
        all_detections = self.detect(frame, timestamp)
        return [d for d in all_detections if d.class_id in self.target_classes]

    def has_bird(self, detections: list[Detection]) -> bool:
        """Check if any detection is a bird."""
        return any(d.class_id in self.target_classes for d in detections)

    @staticmethod
    def get_best_detection(detections: list[Detection]) -> Detection | None:
        """Return highest confidence detection."""
        if not detections:
            return None
        return max(detections, key=lambda d: d.confidence)
