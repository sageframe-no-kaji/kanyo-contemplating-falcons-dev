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


class TestFrame:
    """Tests for Frame dataclass."""

    def test_frame_creation(self):
        """Frame dataclass can be created."""
        from kanyo.detection.capture import Frame

        data = np.zeros((480, 640, 3), dtype=np.uint8)
        frame = Frame(data=data, frame_number=1, width=640, height=480)

        assert frame.frame_number == 1
        assert frame.width == 640
        assert frame.height == 480
        assert frame.shape == (480, 640, 3)


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
