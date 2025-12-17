"""Test logging setup with config integration"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kanyo.utils.config import load_config
from kanyo.utils.logger import setup_logging_from_config, get_logger


def main():
    print("=" * 60)
    print("Testing Logger with Config Integration")
    print("=" * 60)

    # Load config and setup logging from it
    config = load_config("config.yaml")
    setup_logging_from_config(config)

    print(f"\n✅ Logging configured from config.yaml")
    print(f"   Level: {config.get('log_level')}")
    print(f"   File:  {config.get('log_file')}\n")

    logger = get_logger(__name__)

    print("Sending test messages at each level:")
    logger.debug("DEBUG: This won't show at INFO level")
    logger.info("INFO: Application started")
    logger.warning("WARNING: Something might be wrong")
    logger.error("ERROR: Something went wrong")

    print(f"\n✅ Check {config.get('log_file')} for logged messages")
    print("=" * 60)


if __name__ == "__main__":
    main()
