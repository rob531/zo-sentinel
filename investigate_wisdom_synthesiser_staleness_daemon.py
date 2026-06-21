#!/usr/bin/env python3
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('wisdom_synthesiser_staleness.log'),
        logging.StreamHandler()
    ]
)

# Constants
HEARTBEAT_FILE = Path('/var/run/wisdom_synthesiser/heartbeat')
STALENESS_THRESHOLD = timedelta(minutes=5)  # 5 minutes threshold

def check_heartbeat_staleness():
    """Check if the wisdom_synthesiser daemon is stale based on its heartbeat."""
    try:
        # Read the heartbeat timestamp
        with open(HEARTBEAT_FILE, 'r') as f:
            last_heartbeat = float(f.read().strip())

        # Calculate the time since last heartbeat
        current_time = time.time()
        time_since_heartbeat = current_time - last_heartbeat

        # Convert to timedelta for comparison
        time_since_heartbeat_delta = timedelta(seconds=time_since_heartbeat)

        # Check if the heartbeat is stale
        if time_since_heartbeat_delta > STALENESS_THRESHOLD:
            logging.warning(
                f"wisdom_synthesiser daemon is stale. "
                f"Last heartbeat was at {datetime.fromtimestamp(last_heartbeat)}, "
                f"which is {time_since_heartbeat_delta} over the threshold."
            )
            return True
        else:
            logging.info(
                f"wisdom_synthesiser daemon is active. "
                f"Last heartbeat was at {datetime.fromtimestamp(last_heartbeat)}, "
                f"which is within the threshold."
            )
            return False
    except FileNotFoundError:
        logging.error(f"Heartbeat file not found at {HEARTBEAT_FILE}. Daemon may not be running.")
        return True
    except Exception as e:
        logging.error(f"An error occurred while checking heartbeat: {e}")
        return True

if __name__ == "__main__":
    logging.info("Starting wisdom_synthesiser staleness check...")
    is_stale = check_heartbeat_staleness()
    if is_stale:
        logging.info("Exiting with status 1 (stale).")
        exit(1)
    else:
        logging.info("Exiting with status 0 (active).")
        exit(0)