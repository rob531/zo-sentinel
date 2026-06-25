#!/usr/bin/env python3
import logging
import requests
import sqlite3
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
DB_PATH = '/var/lib/zo-sentinel/zo-sentinel.db'
WRITE_SERVICE_HEALTH_ENDPOINT = 'http://localhost:8080/health'
MAX_HEARTBEAT_AGE_SECONDS = 30
WRITE_SERVICE_NAME = 'write_service'

def check_database_heartbeat():
    """Check the last heartbeat time for write_service in the database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT last_heartbeat FROM service_health
            WHERE service_name = ?
        """, (WRITE_SERVICE_NAME,))

        result = cursor.fetchone()
        conn.close()

        if not result:
            logger.error(f"No heartbeat record found for {WRITE_SERVICE_NAME}")
            return None

        last_heartbeat = datetime.fromisoformat(result[0])
        current_time = datetime.now()
        heartbeat_age = (current_time - last_heartbeat).total_seconds()

        if heartbeat_age > MAX_HEARTBEAT_AGE_SECONDS:
            logger.warning(f"Heartbeat is stale. Last heartbeat was {heartbeat_age:.2f} seconds ago")
            return False
        else:
            logger.info(f"Heartbeat is fresh. Last heartbeat was {heartbeat_age:.2f} seconds ago")
            return True

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None

def check_write_service_health():
    """Check the health of the write_service by making a request to its health endpoint."""
    try:
        response = requests.get(WRITE_SERVICE_HEALTH_ENDPOINT, timeout=5)
        if response.status_code == 200:
            logger.info("write_service health endpoint is responsive")
            return True
        else:
            logger.error(f"write_service health endpoint returned status code {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to write_service health endpoint: {e}")
        return False

def diagnose_write_service_staleness():
    """Diagnose the root cause of write_service staleness."""
    logger.info("Starting diagnosis of write_service staleness...")

    # Check database heartbeat
    heartbeat_status = check_database_heartbeat()
    if heartbeat_status is None:
        logger.error("Cannot determine heartbeat status due to database issues")
        return

    # Check write_service health
    health_status = check_write_service_health()

    # Diagnose the issue
    if not heartbeat_status and not health_status:
        logger.error("CRITICAL: write_service is not responding and heartbeat is stale. Likely crashed or network issues.")
    elif not heartbeat_status and health_status:
        logger.error("WARNING: write_service is responsive but heartbeat is stale. Likely excessive load preventing heartbeats.")
    elif heartbeat_status and not health_status:
        logger.error("WARNING: write_service is not responding but heartbeat is fresh. Likely temporary network issues.")
    else:
        logger.info("write_service is healthy and heartbeat is fresh. No issues detected.")

if __name__ == "__main__":
    diagnose_write_service_staleness()