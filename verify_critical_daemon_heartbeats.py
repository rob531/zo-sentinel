import requests
import time
import json

# Configuration
WRITE_SERVICE_URL = "http://localhost:8080/write_service"  # Replace with your actual write_service URL
CRITICAL_DAEMONS = [
    "write_service",
    "mcp_scanner",
    "signal_analyser",
    "trust_synthesiser",
    "threat_intel_ingestor",
    "attestation_engine",
    "risk_ranker",
    "self_diagnostics",
    "rug_pull_monitor",
    "anti_entropy",
    "wisdom_synthesiser",
]
MAX_HEARTBEAT_AGE_SECONDS = 5 * 60  # 5 minutes

def verify_daemon_heartbeats():
    """
    Verifies the heartbeat status of all critical ZoSentinel daemons.
    """
    print("Starting critical daemon heartbeat verification...")

    try:
        payload = {
            "query": "service_health",
            "filters": {"daemon_name": CRITICAL_DAEMONS},
            "fields": ["daemon_name", "last_heartbeat", "status"],
        }
        response = requests.post(WRITE_SERVICE_URL, json=payload)
        response.raise_for_status()  # Raise an exception for bad status codes
        health_data = response.json()

    except requests.exceptions.RequestException as e:
        print(f"FAIL: Failed to query write_service: {e}")
        return 1
    except json.JSONDecodeError:
        print(f"FAIL: Invalid JSON response from write_service: {response.text}")
        return 1

    if not health_data or "data" not in health_data:
        print(f"FAIL: Unexpected response format from write_service: {health_data}")
        return 1

    daemon_health_map = {item["daemon_name"]: item for item in health_data["data"]}
    all_healthy = True
    current_time = time.time()

    for daemon in CRITICAL_DAEMONS:
        if daemon not in daemon_health_map:
            print(f"FAIL: Daemon '{daemon}' not found in service_health data.")
            all_healthy = False
            continue

        health_info = daemon_health_map[daemon]
        status = health_info.get("status")
        last_heartbeat_str = health_info.get("last_heartbeat")

        if not status:
            print(f"FAIL: Daemon '{daemon}' has no status reported.")
            all_healthy = False
            continue

        if status not in ["running", "healthy"]:
            print(f"FAIL: Daemon '{daemon}' has unexpected status: '{status}'.")
            all_healthy = False
            continue

        if not last_heartbeat_str:
            print(f"FAIL: Daemon '{daemon}' has no last_heartbeat reported.")
            all_healthy = False
            continue

        try:
            # Assuming last_heartbeat is in ISO 8601 format with timezone
            # Example: "2023-10-27T10:30:00.123456Z"
            # We need to parse it and convert to a timestamp
            # A more robust solution might use dateutil.parser
            last_heartbeat_ts = time.mktime(time.strptime(last_heartbeat_str[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            print(f"FAIL: Daemon '{daemon}' has unparseable last_heartbeat format: '{last_heartbeat_str}'.")
            all_healthy = False
            continue

        heartbeat_age = current_time - last_heartbeat_ts
        if heartbeat_age > MAX_HEARTBEAT_AGE_SECONDS:
            print(f"FAIL: Daemon '{daemon}' heartbeat is too old. Last heartbeat: {last_heartbeat_str} ({heartbeat_age:.0f}s ago).")
            all_healthy = False
            continue

        print(f"PASS: Daemon '{daemon}' is healthy. Status: '{status}', Last heartbeat: {last_heartbeat_str}")

    if all_healthy:
        print("\nOverall: PASS - All critical daemons are healthy and reporting recent heartbeats.")
        return 0
    else:
        print("\nOverall: FAIL - One or more critical daemons are not healthy or have stale heartbeats.")
        return 1

if __name__ == "__main__":
    exit_code = verify_daemon_heartbeats()
    exit(exit_code)