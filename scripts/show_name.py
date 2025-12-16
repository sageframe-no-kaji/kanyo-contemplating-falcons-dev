"""Show what __name__ contains"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kanyo.utils.logger import get_logger

print(f"The value of __name__ is: {__name__}")

logger = get_logger(__name__)
logger.info("Look at the log line - see where __name__ appears?")
