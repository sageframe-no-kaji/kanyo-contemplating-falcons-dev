"""Tests for kanyo.detection.detect helpers and the package's lazy loading.

Covers Detection serialization, target-class selection, detection helpers,
and the lazy YOLO model load. The model is never loaded for real: the
``ultralytics`` module is replaced in sys.modules, so no weights are read.
YOLO inference itself is specified in test_detection.py.
"""

import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from kanyo.detection.detect import (
    ANIMAL_CLASS_IDS,
    BIRD_CLASS_ID,
    Detection,
    FalconDetector,
)

TS = datetime(2026, 7, 16, 12, 0, 0)


def make_detection(
    class_id: int = BIRD_CLASS_ID,
    class_name: str = "bird",
    confidence: float = 0.8,
    bbox: tuple[int, int, int, int] = (10, 20, 30, 40),
) -> Detection:
    return Detection(
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        bbox=bbox,
        timestamp=TS,
    )


class TestDetectionSerialization:
    """Detection.to_dict produces JSON-ready values."""

    def test_to_dict_rounds_confidence_and_isoformats_timestamp(self):
        detection = make_detection(confidence=0.87654)
        d = detection.to_dict()

        assert d == {
            "class_id": BIRD_CLASS_ID,
            "class_name": "bird",
            "confidence": 0.877,
            "bbox": (10, 20, 30, 40),
            "timestamp": "2026-07-16T12:00:00",
        }


class TestTargetClassSelection:
    """__init__ picks target classes from the detect_any_animal switch."""

    def test_any_animal_mode_defaults_to_all_animal_classes(self):
        detector = FalconDetector(detect_any_animal=True)
        assert detector.target_classes == list(ANIMAL_CLASS_IDS.keys())

    def test_bird_only_mode_defaults_to_bird_class(self):
        detector = FalconDetector(detect_any_animal=False)
        assert detector.target_classes == [BIRD_CLASS_ID]

    def test_bird_only_mode_honors_explicit_target_classes(self):
        detector = FalconDetector(detect_any_animal=False, target_classes=[14, 15])
        assert detector.target_classes == [14, 15]


class TestLazyModelLoading:
    """The YOLO model loads on first .model access and is cached after."""

    def test_model_loads_once_from_model_path(self):
        fake_ultralytics = MagicMock()
        fake_model = object()
        fake_ultralytics.YOLO.return_value = fake_model

        detector = FalconDetector(model_path="models/yolov8n.pt")
        assert detector._model is None  # nothing loaded at construction

        with patch.dict(sys.modules, {"ultralytics": fake_ultralytics}):
            first = detector.model
            second = detector.model

        assert first is fake_model
        assert second is fake_model
        fake_ultralytics.YOLO.assert_called_once_with("models/yolov8n.pt")


class TestDetectionHelpers:
    """has_bird / get_best_detection operate on detection lists only."""

    def test_has_bird_true_for_target_class(self):
        detector = FalconDetector(detect_any_animal=False)
        detections = [
            make_detection(class_id=15, class_name="cat"),
            make_detection(class_id=BIRD_CLASS_ID, class_name="bird"),
        ]
        assert detector.has_bird(detections) is True

    def test_has_bird_false_for_non_targets_or_empty(self):
        detector = FalconDetector(detect_any_animal=False)
        assert detector.has_bird([make_detection(class_id=15, class_name="cat")]) is False
        assert detector.has_bird([]) is False

    def test_get_best_detection_returns_highest_confidence(self):
        low = make_detection(confidence=0.4)
        high = make_detection(confidence=0.9)
        assert FalconDetector.get_best_detection([low, high]) is high

    def test_get_best_detection_empty_returns_none(self):
        assert FalconDetector.get_best_detection([]) is None


class TestDetectionPackageLazyAttrs:
    """kanyo.detection exposes heavy classes lazily via __getattr__."""

    def test_lazy_attribute_resolves_to_real_class(self):
        import kanyo.detection

        assert kanyo.detection.Detection is Detection

    def test_unknown_attribute_raises_attribute_error(self):
        import kanyo.detection

        with pytest.raises(AttributeError, match="has no attribute 'nonexistent'"):
            kanyo.detection.nonexistent  # noqa: B018
