#!/usr/bin/env python3

import argparse
import logging
import time
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_THRESHOLD = 300  # 5 minutes in seconds
DEFAULT_HEARTBEAT_FILE = '/var/lib/zo-sentinel/anti_entropy_heartbeat'

def get_last_heartbeat_timestamp(heartbeat_file: str) -> Optional[float]:
    """Read the last heartbeat timestamp from the file."""
    try:
        with open(heartbeat_file, 'r') as f:
            timestamp = float(f.read().strip())
            return timestamp
    except (IOError, ValueError) as e:
        logger.error(f"Error reading heartbeat file: {e}")
        return None

def check_anti_entropy_staleness(
    heartbeat_file: str,
    threshold: int = DEFAULT_THRESHOLD
) -> bool:
    """Check if the anti-entropy daemon is stale."""
    last_heartbeat = get_last_heartbeat_timestamp(heartbeat_file)
    if last_heartbeat is None:
        logger.error("Could not read heartbeat timestamp.")
        return True

    current_time = time.time()
    staleness = current_time - last_heartbeat

    if staleness > threshold:
        logger.warning(
            f"Anti-entropy daemon is stale. Last heartbeat: {last_heartbeat}, "
            f"Current time: {current_time}, Staleness: {staleness:.2f} seconds"
        )
        return True
    else:
        logger.info(
            f"Anti-entropy daemon is healthy. Last heartbeat: {last_heartbeat}, "
            f"Current time: {current_time}, Staleness: {staleness:.2f} seconds"
        )
        return False

def main():
    parser = argparse.ArgumentParser(
        description='Investigate the staleness of the anti-entropy daemon.'
    )
    parser.add_argument(
        '--heartbeat-file',
        default=DEFAULT_HEARTBEAT_FILE,
        help='Path to the heartbeat file (default: %(default)s)'
    )
    parser.add_argument(
        '--threshold',
        type=int,
        default=DEFAULT_THRESHOLD,
        help='Threshold in seconds for staleness (default: %(default)s)'
    )
    args = parser.parse_args()

    is_stale = check_anti_entropy_staleness(args.heartbeat_file, args.threshold)
    exit(1 if is_stale else 0)

if __name__ == '__main__':
    main()