"""
kanyo.detection: Falcon detection and event tracking.

Modules:
    detect: FalconDetector class for YOLOv8 inference
    events: Event models and persistence (FalconVisit, EventStore)
    capture: Video capture utilities
    buffer_monitor: Live stream monitoring with buffer-based clip extraction

Heavy submodules (capture, detect) import cv2/ultralytics and are loaded
lazily so that lightweight consumers (e.g. tests of events/config) can
import this package without OpenCV installed.
"""

from typing import TYPE_CHECKING, Any

from kanyo.detection.events import EventRecord, EventStore, FalconVisit

if TYPE_CHECKING:
    from kanyo.detection.capture import Frame, StreamCapture
    from kanyo.detection.detect import Detection, FalconDetector

__all__ = [
    "StreamCapture",
    "Frame",
    "FalconDetector",
    "Detection",
    "EventRecord",
    "FalconVisit",
    "EventStore",
]

_LAZY_ATTRS = {
    "StreamCapture": ("kanyo.detection.capture", "StreamCapture"),
    "Frame": ("kanyo.detection.capture", "Frame"),
    "FalconDetector": ("kanyo.detection.detect", "FalconDetector"),
    "Detection": ("kanyo.detection.detect", "Detection"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    from importlib import import_module

    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
