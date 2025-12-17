"""
kanyo.detection: Falcon detection and event tracking.

Modules:
    detect: FalconDetector class for YOLOv8 inference
    events: Event models and persistence (FalconVisit, EventStore)
    capture: Video capture utilities
    realtime_monitor: Live stream monitoring
"""

from kanyo.detection.capture import StreamCapture, Frame
from kanyo.detection.detect import FalconDetector, Detection
from kanyo.detection.events import FalconEvent, FalconVisit, EventStore

__all__ = [
    "StreamCapture",
    "Frame",
    "FalconDetector",
    "Detection",
    "FalconEvent",
    "FalconVisit",
    "EventStore",
]
