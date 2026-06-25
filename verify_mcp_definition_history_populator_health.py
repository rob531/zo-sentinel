import os
import requests
import datetime
import time

# --- Configuration ---
# Base URL for the write_service. This should be set as an environment variable
# in a production environment. For local testing, you might hardcode it or
# provide a default.
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://localhost:8000") # Default for local testing

# Thresholds
HEARTBEAT_STALE_THRESHOLD_SECONDS = 60
MCP_HISTORY_RECENCY_THRESHOLD_SECONDS = 300  # 5 minutes

# Service name to monitor
MCP_POPULATOR_SERVICE_NAME = "mcp_definition_history_populator"

# --- Helper Functions ---
def _query_write_service(table_name: str, filters: dict = None, order_by: list = None, limit: int = None) -> list:
    """
    Queries the write_service for data from a specified table.

    Args:
        table_name: The name of the table to query.
        filters: A dictionary of filters to apply (e.g., {"column_name": "value"}).
        order_by: A list of dictionaries for ordering (e.g., [{"column": "col", "direction": "desc"}]).
        limit: An integer to limit the number of results.

    Returns:
        A list of dictionaries, where each dictionary represents a row.
        Returns an empty list if no data or an error occurs.
    """
    url = f"{WRITE_SERVICE_URL}/query_table"
    payload = {"table_name": table_name}
    if filters:
        payload["filters"] = filters
    if order_by:
        payload["order_by"] = order_by
    if limit is not None:
        payload["limit"] = limit

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()
        if data and "records" in data and isinstance(data["records"], list):
            return data["records"]
        print(f"WARNING: Unexpected response format from {url}: {data}")
        return []
    except requests.exceptions.Timeout:
        print(f"ERROR: Request to {url} timed out.")
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Could not connect to write_service at {WRITE_SERVICE_URL}.")
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP error querying {table_name} from write_service: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        print(f"ERROR: An unexpected request error occurred: {e}")
    except ValueError:
        print(f"ERROR: Failed to decode JSON from response for {url}.")
    return []

def run_check() -> bool:
    """
    Performs the health verification for mcp_definition_history_populator.

    Checks the service heartbeat and the recency of entries in mcp_definition_history.

    Returns:
        True if all checks pass, False otherwise.
    """
    print(f"--- Starting health check for {MCP_POPULATOR_SERVICE_NAME} ---")
    all_checks_passed = True

    # --- Check 1: Service Heartbeat ---
    print(f"Checking heartbeat for service: {MCP_POPULATOR_SERVICE_NAME}...")
    service_health_records = _query_write_service(
        table_name="service_health",
        filters={"service_name": MCP_POPULATOR_SERVICE_NAME}
    )

    if not service_health_records:
        print(f"FAILURE: No heartbeat record found for {MCP_POPULATOR_SERVICE_NAME}.")
        all_checks_passed = False
    else:
        heartbeat_record = service_health_records[0]
        last_heartbeat_str = heartbeat_record.get("last_heartbeat")
        if not last_heartbeat_str:
            print(f"FAILURE: 'last_heartbeat' field missing for {MCP_POPULATOR_SERVICE_NAME}.")
            all_checks_passed = False
        else:
            try:
                # Assuming last_heartbeat is an ISO formatted string (e.g., "2023-10-27T10:00:00.123456Z")
                # Python's datetime.fromisoformat handles 'Z' if it's replaced with '+00:00'
                if last_heartbeat_str.endswith('Z'):
                    last_heartbeat_str = last_heartbeat_str[:-1] + '+00:00'
                last_heartbeat_time = datetime.datetime.fromisoformat(last_heartbeat_str)
                current_time = datetime.datetime.now(datetime.timezone.utc) # Ensure timezone awareness
                
                # If last_heartbeat_time is naive, assume UTC for comparison
                if last_heartbeat_time.tzinfo is None:
                    last_heartbeat_time = last_heartbeat_time.replace(tzinfo=datetime.timezone.utc)

                time_diff = (current_time - last_heartbeat_time).total_seconds()

                if time_diff > HEARTBEAT_STALE_THRESHOLD_SECONDS:
                    print(f"FAILURE: Heartbeat for {MCP_POPULATOR_SERVICE_NAME} is stale. "
                          f"Last heartbeat: {last_heartbeat_str} ({time_diff:.2f} seconds ago). "
                          f"Threshold: {HEARTBEAT_STALE_THRESHOLD_SECONDS} seconds.")
                    all_checks_passed = False
                else:
                    print(f"SUCCESS: Heartbeat for {MCP_POPULATOR_SERVICE_NAME} is fresh. "
                          f"Last heartbeat: {last_heartbeat_str} ({time_diff:.2f} seconds ago).")
            except ValueError:
                print(f"FAILURE: Invalid 'last_heartbeat' format for {MCP_POPULATOR_SERVICE_NAME}: {last_heartbeat_str}")
                all_checks_passed = False

    # --- Check 2: mcp_definition_history population ---
    print("Checking mcp_definition_history for recent entries...")
    mcp_history_records = _query_write_service(
        table_name="mcp_definition_history",
        order_by=[{"column": "created_at", "direction": "desc"}],
        limit=1
    )

    if not mcp_history_records:
        print("FAILURE: mcp_definition_history table is empty or no records found.")
        all_checks_passed = False
    else:
        latest_entry = mcp_history_records[0]
        created_at_str = latest_entry.get("created_at")
        if not created_at_str:
            print("FAILURE: 'created_at' field missing in the latest mcp_definition_history entry.")
            all_checks_passed = False
        else:
            try:
                if created_at_str.endswith('Z'):
                    created_at_str = created_at_str[:-1] + '+00:00'
                latest_created_at_time = datetime.datetime.fromisoformat(created_at_str)
                current_time = datetime.datetime.now(datetime.timezone.utc)

                if latest_created_at_time.tzinfo is None:
                    latest_created_at_time = latest_created_at_time.replace(tzinfo=datetime.timezone.utc)

                time_diff = (current_time - latest_created_at_time).total_seconds()

                if time_diff > MCP_HISTORY_RECENCY_THRESHOLD_SECONDS:
                    print(f"FAILURE: Latest entry in mcp_definition_history is stale. "
                          f"Created at: {created_at_str} ({time_diff:.2f} seconds ago). "
                          f"Threshold: {MCP_HISTORY_RECENCY_THRESHOLD_SECONDS} seconds.")
                    all_checks_passed = False
                else:
                    print(f"SUCCESS: mcp_definition_history is being populated with recent entries. "
                          f"Latest entry created at: {created_at_str} ({time_diff:.2f} seconds ago).")
            except ValueError:
                print(f"FAILURE: Invalid 'created_at' format in mcp_definition_history: {created_at_str}")
                all_checks_passed = False

    print(f"--- Health check for {MCP_POPULATOR_SERVICE_NAME} finished ---")
    return all_checks_passed

if __name__ == "__main__":
    print(f"Using WRITE_SERVICE_URL: {WRITE_SERVICE_URL}")
    if run_check():
        print("PASS: All health checks for mcp_definition_history_populator passed successfully.")
    else:
        print("FAIL: One or more health checks for mcp_definition_history_populator failed.")
        # In a real system, you might exit with a non-zero status code here
        # import sys
        # sys.exit(1)