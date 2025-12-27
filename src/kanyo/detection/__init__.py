"""
kanyo.detection: Falcon detection and event tracking.

Modules:
    detect: FalconDetector class for YOLOv8 inference
    events: Event models and persistence (FalconVisit, EventStore)
    capture: Video capture utilities
    buffer_monitor: Live stream monitoring with buffer-based clip extraction
"""

from kanyo.detection.capture import Frame, StreamCapture
from kanyo.detection.detect import Detection, FalconDetector
from kanyo.detection.events import EventStore, EventRecord, FalconVisit

__all__ = [
    "StreamCapture",
    "Frame",
    "FalconDetector",
    "Detection",
    "EventRecord",
    "FalconVisit",
    "EventStore",
]
