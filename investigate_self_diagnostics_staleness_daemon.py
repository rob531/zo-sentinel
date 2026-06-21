#!/usr/bin/env python3
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Constants
LOG_FILE = "/var/log/zo-sentinel/self_diagnostics.log"
HEARTBEAT_THRESHOLD_MINUTES = 30
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

def setup_logging():
    """Configure logging to both console and file."""
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )

def get_last_heartbeat(log_file: str) -> Optional[datetime]:
    """
    Extract the last heartbeat timestamp from the log file.

    Args:
        log_file: Path to the log file.

    Returns:
        The last heartbeat timestamp as a datetime object, or None if not found.
    """
    try:
        with open(log_file, 'r') as f:
            for line in reversed(list(f)):
                if "Heartbeat" in line:
                    # Extract timestamp from log line (assuming format: "YYYY-MM-DD HH:MM:SS - INFO - Heartbeat")
                    timestamp_str = line.split(" - ")[0]
                    return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    except (FileNotFoundError, IndexError, ValueError) as e:
        logging.error(f"Error reading log file: {e}")
    return None

def check_heartbeat_staleness(last_heartbeat: datetime) -> bool:
    """
    Check if the last heartbeat is older than the acceptable threshold.

    Args:
        last_heartbeat: The last heartbeat timestamp.

    Returns:
        True if the heartbeat is stale, False otherwise.
    """
    current_time = datetime.now()
    threshold = timedelta(minutes=HEARTBEAT_THRESHOLD_MINUTES)
    return (current_time - last_heartbeat) > threshold

def main():
    setup_logging()
    logging.info("Starting self_diagnostics staleness investigation...")

    last_heartbeat = get_last_heartbeat(LOG_FILE)
    if last_heartbeat is None:
        logging.error("No heartbeat found in the log file.")
        return

    logging.info(f"Last heartbeat detected at: {last_heartbeat}")

    if check_heartbeat_staleness(last_heartbeat):
        logging.warning(
            f"Heartbeat is stale! Last heartbeat was {datetime.now() - last_heartbeat} "
            f"ago (threshold: {HEARTBEAT_THRESHOLD_MINUTES} minutes)."
        )
    else:
        logging.info("Heartbeat is fresh. No issues detected.")

if __name__ == "__main__":
    main()