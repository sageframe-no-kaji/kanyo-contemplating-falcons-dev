"""Test configuration loading"""
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kanyo.utils.config import load_config

def main():
    config = load_config("config.yaml")

    print("Configuration loaded successfully!")
    print(f"Video source: {config['video_source']}")
    print(f"Detection confidence: {config['detection_confidence']}")
    print(f"Detection interval: {config['detection_interval']}s")
    print(f"Model path: {config['model_path']}")
    print(f"Output directory: {config['output_dir']}")

if __name__ == "__main__":
    main()
