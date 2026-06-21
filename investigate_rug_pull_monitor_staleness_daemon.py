#!/usr/bin/env python3
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("rug_pull_monitor_staleness.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Constants
HEARTBEAT_FILE = Path("/var/run/rug_pull_monitor/heartbeat")
STALENESS_THRESHOLD = timedelta(minutes=30)  # 30 minutes threshold

def get_last_heartbeat() -> Optional[datetime]:
    """Read the last heartbeat timestamp from the heartbeat file."""
    try:
        with open(HEARTBEAT_FILE, "r") as f:
            timestamp_str = f.read().strip()
            return datetime.fromtimestamp(float(timestamp_str))
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error reading heartbeat file: {e}")
        return None

def check_staleness() -> bool:
    """Check if the daemon is stale based on the last heartbeat."""
    last_heartbeat = get_last_heartbeat()
    if last_heartbeat is None:
        logger.error("Could not determine last heartbeat. Daemon may not be running.")
        return True

    current_time = datetime.now()
    staleness = current_time - last_heartbeat

    if staleness > STALENESS_THRESHOLD:
        logger.warning(
            f"Daemon is stale. Last heartbeat: {last_heartbeat}, "
            f"Current time: {current_time}, Staleness: {staleness}"
        )
        return True
    else:
        logger.info(
            f"Daemon is healthy. Last heartbeat: {last_heartbeat}, "
            f"Current time: {current_time}, Staleness: {staleness}"
        )
        return False

def main():
    """Main function to execute the staleness check."""
    logger.info("Starting rug_pull_monitor staleness check...")
    is_stale = check_staleness()

    if is_stale:
        logger.error("Daemon is stale. Taking corrective action if needed.")
        # Add any corrective actions here, e.g., restarting the daemon
    else:
        logger.info("Daemon is healthy. No action needed.")

if __name__ == "__main__":
    main()