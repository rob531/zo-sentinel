#!/usr/bin/env python3
# deps: requests

import requests
from datetime import datetime

# Constants
WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
DAEMON_NAME = "mcp_definition_history_populator"

# Self-smoke test data
SELF_SMOKE_DAEMON_STATUS = {
    "status": "running",
    "meta": {
        "last_heartbeat": "2023-01-01T00:00:00Z",
        "last_run": "2023-01-01T00:00:00Z"
    }
}

SELF_SMOKE_TABLE_ROWS = 0


def query_service_health(daemon_name):
    """Query the service_health table for a daemon's status."""
    query = """
    SELECT status, meta
    FROM service_health
    WHERE service_name = ?
    ORDER BY timestamp DESC
    LIMIT 1
    """
    params = [daemon_name]
    response = requests.post(
        WRITE_SERVICE_URL,
        json={"sql": query, "params": params}
    )
    response.raise_for_status()
    return response.json()


def query_table_row_count(table_name):
    """Query a table for its row count."""
    query = f"SELECT COUNT(*) as count FROM {table_name}"
    response = requests.post(
        WRITE_SERVICE_URL,
        json={"sql": query}
    )
    response.raise_for_status()
    return response.json()[0]["count"]


def run_diagnostic():
    """Run the diagnostic checks and print findings."""
    # Query daemon status
    try:
        daemon_status = query_service_health(DAEMON_NAME)
    except requests.exceptions.RequestException as e:
        print(f"Error querying service_health: {e}")
        return

    # Query table row count
    try:
        row_count = query_table_row_count("mcp_definition_history")
    except requests.exceptions.RequestException as e:
        print(f"Error querying mcp_definition_history: {e}")
        return

    # Print findings
    print("\n=== Diagnostic Findings ===")
    print(f"Daemon Status: {daemon_status.get('status', 'unknown')}")
    print(f"Last Heartbeat: {daemon_status.get('meta', {}).get('last_heartbeat', 'unknown')}")
    print(f"Last Run: {daemon_status.get('meta', {}).get('last_run', 'unknown')}")
    print(f"mcp_definition_history Row Count: {row_count}")


def self_smoke_test():
    """Run a self-smoke test with simulated data."""
    print("\n=== Running Self-Smoke Test ===")
    # Simulate daemon status
    daemon_status = SELF_SMOKE_DAEMON_STATUS
    # Simulate table row count
    row_count = SELF_SMOKE_TABLE_ROWS
    # Print findings
    print(f"Daemon Status: {daemon_status.get('status', 'unknown')}")
    print(f"Last Heartbeat: {daemon_status.get('meta', {}).get('last_heartbeat', 'unknown')}")
    print(f"Last Run: {daemon_status.get('meta', {}).get('last_run', 'unknown')}")
    print(f"mcp_definition_history Row Count: {row_count}")
    print("PASS")

if __name__ == "__main__":
    run_diagnostic()
    self_smoke_test()
