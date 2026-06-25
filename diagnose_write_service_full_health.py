import requests
import time
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
SERVICE_HEALTH_URL = "http://127.0.0.1:8772/service_health"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
TEST_TABLE = "test_table"
MAX_HEARTBEAT_STALENESS = 60  # seconds

def check_heartbeat():
    try:
        response = requests.post(SERVICE_HEALTH_URL, json={"service": "write_service"})
        response.raise_for_status()
        heartbeat_time = response.json().get("last_heartbeat")
        if not heartbeat_time:
            logger.error("No heartbeat time found in the response.")
            return False

        current_time = time.time()
        staleness = current_time - heartbeat_time
        if staleness > MAX_HEARTBEAT_STALENESS:
            logger.error(f"Heartbeat is stale. Last heartbeat was {staleness:.2f} seconds ago.")
            return False

        logger.info(f"Heartbeat is recent. Last heartbeat was {staleness:.2f} seconds ago.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to query service health: {e}")
        return False

def test_write_operation():
    try:
        query = f"INSERT INTO {TEST_TABLE} (id, data) VALUES (1, 'test') ON CONFLICT (id) DO NOTHING;"
        response = requests.post(WRITE_SERVICE_URL, json={"query": query})
        response.raise_for_status()
        logger.info("Test write operation successful.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Test write operation failed: {e}")
        return False

def test_read_operation():
    try:
        query = f"SELECT * FROM {TEST_TABLE} WHERE id = 1;"
        response = requests.post(QUERY_SERVICE_URL, json={"query": query})
        response.raise_for_status()
        result = response.json().get("result")
        if not result:
            logger.error("No result found in the response.")
            return False

        logger.info("Test read operation successful.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Test read operation failed: {e}")
        return False

def main():
    logger.info("Starting write_service full health check.")

    heartbeat_check = check_heartbeat()
    write_check = test_write_operation()
    read_check = test_read_operation()

    if heartbeat_check and write_check and read_check:
        logger.info("PASS: All checks passed successfully.")
        sys.exit(0)
    else:
        logger.error("FAIL: One or more checks failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()