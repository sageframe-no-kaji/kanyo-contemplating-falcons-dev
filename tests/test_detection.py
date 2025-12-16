"""Tests for detection module"""
import pytest
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def test_imports():
    """Verify detection module imports work"""
    from kanyo.detection import capture, detect, events
    assert capture is not None
    assert detect is not None
    assert events is not None

def test_config_loads():
    """Verify configuration loads"""
    from kanyo.utils.config import load_config
    config = load_config("config.yaml")
    assert config is not None
    assert "detection_confidence" in config
    assert "video_source" in config

# Placeholder tests for future
@pytest.mark.skip(reason="Not implemented yet")
def test_falcon_detection():
    """Test falcon detection on sample frame"""
    pass

@pytest.mark.skip(reason="Not implemented yet")
def test_event_detection():
    """Test enter/exit event detection"""
    pass

