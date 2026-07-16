"""Tests for detection module"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestImports:
    """Verify all detection module imports work."""

    def test_imports(self):
        """Verify detection module imports work"""
        from kanyo.detection import capture, detect, events

        assert capture is not None
        assert detect is not None
        assert events is not None

    def test_public_exports(self):
        """Verify public API exports are available."""
        from kanyo.detection import (
            Detection,
            EventRecord,
            EventStore,
            FalconDetector,
            FalconVisit,
            Frame,
            StreamCapture,
        )

        assert StreamCapture is not None
        assert Frame is not None
        assert FalconDetector is not None
        assert Detection is not None
        assert EventRecord is not None
        assert FalconVisit is not None
        assert EventStore is not None


class TestIRModeDetection:
    """Tests for IR/night mode detection."""

    def test_is_ir_mode_with_grayscale_frame(self):
        """Grayscale/IR frames are detected correctly."""
        from kanyo.detection.detect import is_ir_mode

        # Create grayscale frame (R=G=B)
        grayscale_frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        assert is_ir_mode(grayscale_frame)

    def test_is_ir_mode_with_color_frame(self):
        """Color/daytime frames are detected correctly."""
        from kanyo.detection.detect import is_ir_mode

        # Create color frame with R != G != B
        color_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        color_frame[:, :, 0] = 100  # Blue
        color_frame[:, :, 1] = 150  # Green
        color_frame[:, :, 2] = 200  # Red
        assert not is_ir_mode(color_frame)

    def test_is_ir_mode_boundary(self):
        """IR detection threshold is correct."""
        from kanyo.detection.detect import is_ir_mode

        # Frame with diff=4.9 (just below threshold)
        near_grayscale = np.zeros((480, 640, 3), dtype=np.uint8)
        near_grayscale[:, :, 1] = 100  # G
        near_grayscale[:, :, 2] = 104  # R (diff < 5)
        assert is_ir_mode(near_grayscale)

        # Frame with diff=6 (above threshold)
        color_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        color_frame[:, :, 1] = 100  # G
        color_frame[:, :, 2] = 110  # R (diff > 5)
        assert not is_ir_mode(color_frame)


class TestFalconDetector:
    """Tests for FalconDetector class."""

    def test_instantiation(self):
        """FalconDetector instantiates with lazy loading."""
        from kanyo.detection.detect import FalconDetector

        detector = FalconDetector(confidence_threshold=0.7)
        assert detector.confidence_threshold == 0.7
        assert detector._model is None  # Lazy loading

    def test_instantiation_with_ir_threshold(self):
        """FalconDetector accepts IR threshold parameter."""
        from kanyo.detection.detect import FalconDetector

        detector = FalconDetector(confidence_threshold=0.5, confidence_threshold_ir=0.25)
        assert detector.confidence_threshold == 0.5
        assert detector.confidence_threshold_ir == 0.25

    def test_model_path_default(self):
        """Default model path is set."""
        from kanyo.detection.detect import FalconDetector

        detector = FalconDetector()
        assert str(detector.model_path) == "models/yolov8n.pt"

    def test_detect_on_blank_frame(self):
        """Detection on blank frame returns empty list."""
        from kanyo.detection.detect import FalconDetector

        detector = FalconDetector()
        blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = detector.detect(blank_frame)

        assert isinstance(detections, list)
        assert len(detections) == 0  # No birds in blank frame

    def test_detection_dataclass(self):
        """Detection dataclass has correct fields."""
        from kanyo.detection.detect import Detection

        detection = Detection(
            class_id=14,
            class_name="bird",
            confidence=0.85,
            bbox=(100, 200, 150, 250),
            timestamp=datetime.now(),
        )
        assert detection.class_id == 14
        assert detection.class_name == "bird"
        assert detection.confidence == 0.85
        assert len(detection.bbox) == 4


def _mock_yolo_results(boxes):
    """Build a fake ultralytics results list from (class_id, confidence, bbox) tuples."""
    names = {0: "person", 14: "bird", 15: "cat", 20: "elephant"}
    result = Mock()
    result.names = names
    mock_boxes = []
    for class_id, confidence, bbox in boxes:
        box = Mock()
        box.cls = [class_id]
        box.conf = [confidence]
        xyxy_entry = Mock()
        xyxy_entry.tolist.return_value = list(bbox)
        box.xyxy = [xyxy_entry]
        mock_boxes.append(box)
    result.boxes = mock_boxes
    return [result]


def _color_frame():
    """A frame that is NOT IR mode (R, G, B clearly distinct)."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :, 0] = 100  # Blue
    frame[:, :, 1] = 150  # Green
    frame[:, :, 2] = 200  # Red
    return frame


def _ir_frame():
    """A grayscale frame that IS IR mode (R == G == B)."""
    return np.ones((480, 640, 3), dtype=np.uint8) * 128


class TestDetectWithRaw:
    """Tests for the raw-detections path (024-A): one inference, two views."""

    TS = datetime(2026, 7, 16, 12, 0, 0)

    def _detector(self, **kwargs):
        from kanyo.detection.detect import FalconDetector

        return FalconDetector(confidence_threshold=0.5, **kwargs)

    def test_filtered_raw_split(self):
        """Low-confidence any-class boxes land in raw; filtered keeps today's semantics."""
        detector = self._detector(raw_floor_confidence=0.15)
        detector._model = Mock(
            return_value=_mock_yolo_results(
                [
                    (14, 0.6, (100, 100, 150, 150)),  # bird above threshold -> both
                    (20, 0.2, (200, 200, 300, 300)),  # elephant, low conf -> raw only
                    (0, 0.89, (10, 10, 50, 50)),  # person, high conf, non-target -> raw only
                    (14, 0.1, (400, 400, 420, 420)),  # bird below raw floor -> neither
                ]
            )
        )

        filtered, raw = detector.detect_with_raw(_color_frame(), timestamp=self.TS)

        assert [(d.class_id, d.confidence) for d in filtered] == [(14, 0.6)]
        assert [(d.class_id, d.confidence) for d in raw] == [(14, 0.6), (20, 0.2), (0, 0.89)]
        # Detection objects carry the full field set
        elephant = raw[1]
        assert elephant.class_name == "elephant"
        assert elephant.bbox == (200, 200, 300, 300)
        assert elephant.timestamp == self.TS

    def test_single_inference_at_min_conf(self):
        """Exactly one model call per detect_with_raw, at min(floor, threshold)."""
        detector = self._detector(raw_floor_confidence=0.15)
        model = Mock(return_value=_mock_yolo_results([]))
        detector._model = model

        detector.detect_with_raw(_color_frame(), timestamp=self.TS)

        assert model.call_count == 1
        assert model.call_args.kwargs["conf"] == 0.15

    def test_none_floor_preserves_existing_behavior(self):
        """With raw_floor_confidence=None the model call and outputs match today's."""
        boxes = [
            (14, 0.6, (100, 100, 150, 150)),  # bird, target class
            (20, 0.7, (200, 200, 300, 300)),  # elephant, target class (detect_any_animal)
            (0, 0.89, (10, 10, 50, 50)),  # person, non-target -> excluded
        ]
        detector = self._detector()  # raw_floor_confidence defaults to None
        model = Mock(return_value=_mock_yolo_results(boxes))
        detector._model = model

        detections = detector.detect(_color_frame(), timestamp=self.TS)

        assert model.call_count == 1
        assert model.call_args.kwargs["conf"] == 0.5  # unchanged model-call threshold
        assert [(d.class_id, d.confidence) for d in detections] == [(14, 0.6), (20, 0.7)]

        # detect_birds re-filters to the same set
        model2 = Mock(return_value=_mock_yolo_results(boxes))
        detector._model = model2
        birds = detector.detect_birds(_color_frame(), timestamp=self.TS)
        assert birds == detections

    def test_none_floor_raw_falls_back_to_effective_threshold(self):
        """Unset floor: raw contains any class at or above the effective threshold."""
        detector = self._detector()
        detector._model = Mock(
            return_value=_mock_yolo_results(
                [
                    (14, 0.6, (100, 100, 150, 150)),
                    (0, 0.89, (10, 10, 50, 50)),
                ]
            )
        )

        filtered, raw = detector.detect_with_raw(_color_frame(), timestamp=self.TS)

        assert [(d.class_id, d.confidence) for d in filtered] == [(14, 0.6)]
        assert [(d.class_id, d.confidence) for d in raw] == [(14, 0.6), (0, 0.89)]

    def test_floor_set_filtered_equivalent_to_no_floor(self):
        """Post-filtered view with a floor == historical output without one."""
        # What the model returns at the low floor (superset)
        low_floor_boxes = [
            (14, 0.6, (100, 100, 150, 150)),
            (20, 0.2, (200, 200, 300, 300)),
            (14, 0.3, (400, 400, 420, 420)),
        ]
        # What the model would have returned at conf=0.5 (historical call)
        high_conf_boxes = [(14, 0.6, (100, 100, 150, 150))]

        with_floor = self._detector(raw_floor_confidence=0.15)
        with_floor._model = Mock(return_value=_mock_yolo_results(low_floor_boxes))

        without_floor = self._detector()
        without_floor._model = Mock(return_value=_mock_yolo_results(high_conf_boxes))

        frame = _color_frame()
        assert with_floor.detect(frame, timestamp=self.TS) == without_floor.detect(
            frame, timestamp=self.TS
        )

    def test_ir_mode_threshold_switch_in_filtered_view(self):
        """IR frames use confidence_threshold_ir for the filtered view, unchanged."""
        detector = self._detector(confidence_threshold_ir=0.25, raw_floor_confidence=0.15)
        model = Mock(
            return_value=_mock_yolo_results(
                [
                    (14, 0.3, (100, 100, 150, 150)),  # above IR threshold -> filtered
                    (14, 0.2, (200, 200, 250, 250)),  # below IR threshold -> raw only
                ]
            )
        )
        detector._model = model

        filtered, raw = detector.detect_with_raw(_ir_frame(), timestamp=self.TS)

        assert model.call_args.kwargs["conf"] == 0.15  # min(floor, IR threshold)
        assert [(d.class_id, d.confidence) for d in filtered] == [(14, 0.3)]
        assert [(d.class_id, d.confidence) for d in raw] == [(14, 0.3), (14, 0.2)]

    def test_detect_and_detect_with_raw_agree(self):
        """detect() and detect_with_raw()'s filtered view are the same list."""
        boxes = [
            (14, 0.6, (100, 100, 150, 150)),
            (20, 0.2, (200, 200, 300, 300)),
        ]
        detector = self._detector(raw_floor_confidence=0.15)
        detector._model = Mock(return_value=_mock_yolo_results(boxes))

        frame = _color_frame()
        filtered, _ = detector.detect_with_raw(frame, timestamp=self.TS)
        assert detector.detect(frame, timestamp=self.TS) == filtered


class TestFrame:
    """Tests for Frame dataclass."""

    def test_frame_creation(self):
        """Frame dataclass can be created."""
        from kanyo.detection.capture import Frame

        data = np.zeros((480, 640, 3), dtype=np.uint8)
        stamp = datetime.now()
        frame = Frame(data=data, frame_number=1, width=640, height=480, timestamp=stamp)

        assert frame.frame_number == 1
        assert frame.width == 640
        assert frame.height == 480
        assert frame.shape == (480, 640, 3)
        assert frame.timestamp == stamp


class TestStreamCapture:
    """Tests for StreamCapture class (mocked, no network)."""

    def test_instantiation(self):
        """StreamCapture instantiates correctly."""
        from kanyo.detection.capture import StreamCapture

        cap = StreamCapture(
            stream_url="https://example.com/stream",
            max_height=720,
            reconnect_delay=5.0,
        )
        assert cap.stream_url == "https://example.com/stream"
        assert cap.max_height == 720
        assert not cap.is_connected

    def test_youtube_url_detection(self):
        """YouTube URLs are detected for resolution."""
        from kanyo.detection.capture import StreamCapture

        cap = StreamCapture("https://www.youtube.com/watch?v=abc123")
        assert "youtube.com" in cap.stream_url

    @patch("kanyo.detection.capture.subprocess.run")
    def test_resolve_youtube_url(self, mock_run):
        """YouTube URL resolution calls yt-dlp."""
        from kanyo.detection.capture import StreamCapture

        mock_run.return_value = Mock(returncode=0, stdout="https://direct.url\n")

        cap = StreamCapture("https://www.youtube.com/watch?v=test")
        direct_url = cap.resolve_youtube_url()

        assert direct_url == "https://direct.url"
        mock_run.assert_called_once()
