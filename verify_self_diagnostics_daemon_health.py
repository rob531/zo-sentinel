import time
import sys
from datetime import datetime, timedelta

# --- Configuration ---
SERVICE_NAME = "self_diagnostics"
# Expected interval in seconds within which a heartbeat is considered recent.
# 600 seconds = 10 minutes.
EXPECTED_INTERVAL_SECONDS = 600

# --- Mock Database/Service Interaction ---
# In a real system, this function would interact with a database (e.g., via an ORM)
# or an API endpoint provided by the 'write_service' to fetch the
# 'service_health' table data.
# For this utility, we simulate a healthy 'self_diagnostics' daemon by default.
def _get_service_health_data_mock(service_name: str) -> dict | None:
    """
    Mocks fetching service health data for the given service_name.
    
    Returns a dictionary containing 'service_name', 'last_heartbeat' (timestamp),
    and 'status' if the service is found, otherwise None.
    By default, it simulates a recent heartbeat and a 'healthy' status
    for the 'self_diagnostics' daemon.
    """
    if service_name == SERVICE_NAME:
        # Simulate a heartbeat that occurred 5 minutes ago.
        # This is within our EXPECTED_INTERVAL_SECONDS (10 minutes).
        recent_heartbeat_time = datetime.now() - timedelta(minutes=5)
        return {
            "service_name": SERVICE_NAME,
            "last_heartbeat": recent_heartbeat_time.timestamp(),
            "status": "healthy",
        }
    return None  # Service not found or other error

# --- Main Verification Logic ---
def verify_self_diagnostics_daemon_health():
    """
    Queries the service health for the 'self_diagnostics' daemon and verifies
    its operational status based on the 'last_heartbeat' and 'status' fields.
    Outputs a PASS/FAIL message and sets the appropriate exit code.
    """
    service_data = _get_service_health_data_mock(SERVICE_NAME)

    if not service_data:
        print(f"FAIL: Could not retrieve health data for {SERVICE_NAME} daemon.")
        sys.exit(1)

    last_heartbeat_timestamp = service_data.get("last_heartbeat")
    status = service_data.get("status")

    # Validate the retrieved data types
    if last_heartbeat_timestamp is None or not isinstance(last_heartbeat_timestamp, (int, float)):
        print(f"FAIL: Invalid or missing 'last_heartbeat' for {SERVICE_NAME} daemon.")
        sys.exit(1)
    if status is None or not isinstance(status, str):
        print(f"FAIL: Invalid or missing 'status' for {SERVICE_NAME} daemon.")
        sys.exit(1)

    current_time = time.time()
    time_since_heartbeat = current_time - last_heartbeat_timestamp

    # Check if the heartbeat is within the expected interval
    is_heartbeat_recent = time_since_heartbeat <= EXPECTED_INTERVAL_SECONDS
    
    # Check if the status is one of the healthy states
    is_status_good = status.lower() in ['healthy', 'running']

    if is_heartbeat_recent and is_status_good:
        print("PASS: self_diagnostics daemon is healthy")
        sys.exit(0)
    else:
        print("FAIL: self_diagnostics daemon is stale or unhealthy")
        sys.exit(1)

if __name__ == "__main__":
    verify_self_diagnostics_daemon_health()