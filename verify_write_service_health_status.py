import requests
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
WRITE_SERVICE_URL = "http://localhost:5000/write"
QUERY_SERVICE_URL = "http://localhost:5000/query"
SERVICE_HEALTH_URL = "http://localhost:5000/service_health"
AUDIT_LOG_URL = "http://localhost:5000/audit_log"
TIMEOUT = 5  # seconds

def send_write_request():
    """Send a write request to the write_service."""
    try:
        response = requests.post(WRITE_SERVICE_URL, json={"data": "test_data"}, timeout=TIMEOUT)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Write request failed: {e}")
        return False

def send_query_request():
    """Send a query request to the write_service."""
    try:
        response = requests.get(QUERY_SERVICE_URL, timeout=TIMEOUT)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Query request failed: {e}")
        return False

def check_heartbeat():
    """Check the heartbeat in the service_health table."""
    try:
        response = requests.get(SERVICE_HEALTH_URL, timeout=TIMEOUT)
        response.raise_for_status()
        health_status = response.json()
        if health_status.get("heartbeat") == "alive":
            return True
        else:
            logger.error("Heartbeat is not alive")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Heartbeat check failed: {e}")
        return False

def check_audit_log():
    """Check for error logs in the audit_log."""
    try:
        response = requests.get(AUDIT_LOG_URL, timeout=TIMEOUT)
        response.raise_for_status()
        logs = response.json()
        for log in logs:
            if log.get("level") == "ERROR":
                logger.error(f"Error found in audit log: {log}")
                return False
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Audit log check failed: {e}")
        return False

def verify_write_service_health():
    """Verify the health and responsiveness of the write_service."""
    write_success = send_write_request()
    query_success = send_query_request()
    heartbeat_success = check_heartbeat()
    audit_log_success = check_audit_log()

    if write_success and query_success and heartbeat_success and audit_log_success:
        logger.info("PASS: write_service is healthy")
        return True
    else:
        logger.error("FAIL: write_service is unhealthy")
        return False

if __name__ == "__main__":
    verify_write_service_health()