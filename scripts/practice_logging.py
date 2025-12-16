"""Practice using the logger"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# TODO: Import get_logger
from kanyo.utils.logger import get_logger

# TODO: Get a logger for this module
logger = get_logger(__name__)


def count_to_five():
    """Count from 1 to 5 with logging"""
    # TODO: Log "Starting to count"
    logger.info("Starting to count")

    for i in range(1, 6):
        # TODO: Log each number like "Count: 1", "Count: 2", etc.
        pass
        logger.info(f"Count: {i}")

    # TODO: Log "Finished counting"
    logger.info("Finished counting")


if __name__ == "__main__":
    count_to_five()
