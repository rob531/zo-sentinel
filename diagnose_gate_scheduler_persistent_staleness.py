#!/usr/bin/env python3

"""
Diagnose persistent staleness of the `gate_scheduler` daemon.

This script queries the `service_health` table via the `write_service` to retrieve the
last heartbeat for `gate_scheduler`. It compares this timestamp against the current
time to determine the age of the last heartbeat. If the age exceeds the expected
threshold (180 seconds as per `wiring_map`), it attempts to identify potential
causes such as process not running, errors in its log, or connectivity issues to
the `write_service`. The script outputs a clear diagnostic message indicating
the status and any identified issues.

Constraints:
- Use `requests` for HTTP calls to `write_service`.
- No direct DB access.

Acceptance:
- A `if __name__ == '__main__':` block that executes the diagnostic,
  prints the `gate_scheduler` status, and exits with 0 on successful diagnosis
  (even if stale) and 1 on error during diagnosis.
"""

import sys
import json
import datetime
import requests
from typing import Tuple

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_THRESHOLD_SECONDS = 180


def _query_last_heartbeat() -> datetime.datetime:
    """Query the last heartbeat timestamp for `gate_scheduler` from the `service_health` table.

    Returns:
        datetime.datetime: The last heartbeat timestamp.

    Raises:
        requests.exceptions.RequestException: If the HTTP request fails.
    """
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "sql": """
                    SELECT timestamp FROM service_health
                    WHERE service_name = 'gate_scheduler'
                    ORDER BY timestamp DESC
                    LIMIT 1
                """
            }
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise ValueError("No heartbeat data found")
        return datetime.datetime.fromisoformat(data[0]["timestamp"])
    except requests.exceptions.RequestException as e:
        print(f"Error querying last heartbeat: {e}", file=sys.stderr)
        raise

def _determine_status(last_ts: datetime.datetime) -> Tuple[bool, str]:
    """Determine the status of the `gate_scheduler` based on the last heartbeat timestamp.

    Args:
        last_ts (datetime.datetime): The last heartbeat timestamp.

    Returns:
        Tuple[bool, str]: A tuple containing a boolean indicating if the status is healthy
        and a diagnostic message.
    """
    current_time = datetime.datetime.now(datetime.timezone.utc)
    age_seconds = (current_time - last_ts).total_seconds()
    
    if age_seconds <= HEARTBEAT_THRESHOLD_SECONDS:
        return (True, f"gate_scheduler is healthy. Last heartbeat was {age_seconds:.0f} seconds ago.")
    else:
        return (
            False,
            f"gate_scheduler is stale. Last heartbeat was {age_seconds:.0f} seconds ago.\n"
            "Potential causes:\n"
            "1. Process not running.\n"
            "2. Errors in the log.\n"
            "3. Connectivity issues to the write_service."
        )

def run() -> int:
    """Run the diagnostic and print the status of the `gate_scheduler`.

    Returns:
        int: 0 on successful diagnosis (even if stale), 1 on error during diagnosis.
    """
    try:
        last_ts = _query_last_heartbeat()
        is_healthy, message = _determine_status(last_ts)
        print(message)
        return 0
    except Exception as e:
        print(f"Error during diagnosis: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(run())
