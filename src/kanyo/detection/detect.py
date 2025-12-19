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
        target_classes: list[int] | None = None,
        detect_any_animal: bool = True,
        animal_classes: list[int] | None = None,
    ):
        """
        Initialize detector.

        Args:
            model_path: Path to YOLO model (auto-downloads if missing)
            confidence_threshold: Minimum confidence for detections
            target_classes: Class IDs to detect (default: [14] for birds)
            detect_any_animal: If True, detect any animal (for falcon cams where
                               the model may misclassify falcons as cats/dogs)
            animal_classes: COCO class IDs to treat as "animal" when detect_any_animal=True
        """
        self.model_path = Path(model_path)
        self.confidence_threshold = confidence_threshold
        self.detect_any_animal = detect_any_animal

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

        results = self.model(
            frame,
            conf=self.confidence_threshold,
            verbose=False,
        )

        detections = []
        total_checked = 0
        for result in results:
            for box in result.boxes:
                total_checked += 1
                class_id = int(box.cls[0])
                # Only include detections matching target classes
                if class_id not in self.target_classes:
                    continue
                bbox_list = list(map(int, box.xyxy[0].tolist()))
                bbox: tuple[int, int, int, int] = (
                    bbox_list[0],
                    bbox_list[1],
                    bbox_list[2],
                    bbox_list[3],
                )
                detections.append(
                    Detection(
                        class_id=class_id,
                        class_name=result.names[class_id],
                        confidence=float(box.conf[0]),
                        bbox=bbox,
                        timestamp=timestamp,
                    )
                )

        # Log confidence at DEBUG level
        if detections:
            max_confidence = max(d.confidence for d in detections)
            logger.debug(f"Falcon detected: confidence={max_confidence:.3f}")
        else:
            logger.debug(f"No falcon detected (checked {total_checked} detections)")

        return detections

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
