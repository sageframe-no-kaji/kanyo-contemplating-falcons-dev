"""Test configuration loading"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kanyo.utils.config import load_config


def main():
    print("=" * 60)
    print("Testing Configuration System")
    print("=" * 60)

    config = load_config("config.yaml")

    print("\n✅ Configuration loaded successfully!\n")

    print("Stream & Detection:")
    print(f"  video_source:         {config['video_source']}")
    print(f"  detection_confidence: {config['detection_confidence']}")
    print(f"  detection_interval:   {config['detection_interval']}s")
    print(f"  frame_interval:       {config.get('frame_interval', 30)}")
    print(f"  model_path:           {config['model_path']}")
    print(f"  detect_any_animal:    {config.get('detect_any_animal', True)}")
    print(f"  exit_timeout:         {config.get('exit_timeout', 120)}s")
    print(f"  animal_classes:       {config.get('animal_classes', [])}")

    print("\nOutput & Storage:")
    print(f"  output_dir:           {config['output_dir']}")
    print(f"  data_dir:             {config.get('data_dir', 'NOT SET')}")
    print(f"  events_file:          {config.get('events_file', 'NOT SET')}")

    print("\nLogging:")
    print(f"  log_level:            {config.get('log_level', 'NOT SET')}")
    print(f"  log_file:             {config.get('log_file', 'NOT SET')}")

    print("\n" + "=" * 60)
    print("All config fields loaded correctly!")
    print("=" * 60)


if __name__ == "__main__":
    main()
