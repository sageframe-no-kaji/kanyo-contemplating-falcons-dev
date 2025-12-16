"""Simple test to see logging in action"""

import sys
from pathlib import Path

# Add src to path so we can import kanyo
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kanyo.utils.logger import get_logger

# Get a logger
logger = get_logger(__name__)

# Log some messages
logger.info("This is an INFO message")
logger.warning("This is a WARNING message")
logger.error("This is an ERROR message")

print("\n✓ Check your terminal above - you should see 3 log messages")
print("✓ Check logs/kanyo.log - same 3 messages should be there")
