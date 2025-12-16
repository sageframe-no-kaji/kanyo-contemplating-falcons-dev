"""Test logging setup"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kanyo.utils.logger import get_logger


def main():
    logger = get_logger(__name__)

    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")

    print("\nLogger working! Check logs/kanyo.log")


if __name__ == "__main__":
    main()
