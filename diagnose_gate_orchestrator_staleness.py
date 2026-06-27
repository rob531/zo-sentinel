import requests
import json
from datetime import datetime, timedelta
import sys

# --- Configuration ---
# The URL for the write_service query endpoint.
# This should be replaced with the actual endpoint URL in a production environment.
WRITE_SERVICE_URL = "http://localhost:8080/query" 

# Threshold in seconds to consider a heartbeat stale.
# For example, 5 minutes = 300 seconds.
STALENESS_THRESHOLD_SECONDS = 300 

# --- Helper Functions ---

def _query_write_service(payload: dict) -> dict | None:
    """
    Sends a POST request to the write_service endpoint with the given payload.
    """
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        return response.json()
    except requests.exceptions.Timeout:
        print(f"ERROR: Request to {WRITE_SERVICE_URL} timed out.", file=sys.stderr)
        return None
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Could not connect to {WRITE_SERVICE_URL}. Is the service running?", file=sys.stderr)
        return None
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP error occurred: {e} - Response: {response.text}", file=sys.stderr)
        return None
    except json.JSONDecodeError:
        print(f"ERROR: Failed to decode JSON response from {WRITE_SERVICE_URL}. Response: {response.text}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"ERROR: An unexpected error occurred during DB access: {e}", file=sys.stderr)
        return None

def _get_service_health(service_name: str) -> dict | None:
    """
    Queries the service_health table for a specific service.
    """
    payload = {
        "query_type": "read",
        "table": "service_health",
        "filter": {"service_name": service_name}
    }
    response_data = _query_write_service(payload)

    if response_data and response_data.get("status") == "success" and response_data.get("data"):
        # Assuming 'data' is a list and we want the most recent/relevant entry
        # For service_health, there should ideally be only one entry per service_name
        return response_data["data"][0]
    elif response_data and response_data.get("status") == "success" and not response_data.get("data"):
        print(f"INFO: No health data found for service '{service_name}'.", file=sys.stderr)
    elif response_data:
        print(f"ERROR: Failed to retrieve health data: {response_data.get('message', 'Unknown error')}", file=sys.stderr)
    return None

# --- Main Diagnostic Function ---

def run():
    """
    Diagnoses the staleness of the gate_orchestrator daemon.
    Prints a diagnostic report to stdout.
    """
    report = []
    report.append("--- Gate Orchestrator Staleness Diagnostic Report ---")
    report.append(f"Timestamp of Diagnosis: {datetime.now().isoformat()}")
    report.append(f"Staleness Threshold: {STALENESS_THRESHOLD_SECONDS} seconds")
    report.append("-" * 60)

    service_name = "gate_orchestrator"
    health_data = _get_service_health(service_name)

    if not health_data:
        report.append(f"STATUS: UNKNOWN - Could not retrieve health data for '{service_name}'.")
        report.append("  Potential Cause: The 'write_service' endpoint is unreachable, misconfigured, or the 'service_health' table is empty/inaccessible.")
        report.append("  Recommended Next Steps:")
        report.append(f"    1. Verify that '{WRITE_SERVICE_URL}' is correct and the 'write_service' daemon is running.")
        report.append("    2. Check network connectivity to the 'write_service' endpoint.")
        report.append("    3. Ensure the 'gate_orchestrator' daemon is configured to report its health to 'service_health'.")
        report.append("-" * 60)
        print("\n".join(report))
        return

    report.append(f"Observed Health Data for '{service_name}':")
    for key, value in health_data.items():
        report.append(f"  {key}: {value}")
    report.append("-" * 60)

    last_heartbeat_str = health_data.get("last_heartbeat")
    status = health_data.get("status", "UNKNOWN").upper()
    pid = health_data.get("pid")

    is_stale = False
    time_since_heartbeat = None

    if last_heartbeat_str:
        try:
            # Assuming ISO format (e.g., "2023-10-27T10:30:00Z" or "2023-10-27T10:30:00")
            # Handle potential 'Z' for UTC
            if last_heartbeat_str.endswith('Z'):
                last_heartbeat = datetime.strptime(last_heartbeat_str, "%Y-%m-%dT%H:%M:%SZ")
            else:
                last_heartbeat = datetime.strptime(last_heartbeat_str, "%Y-%m-%dT%H:%M:%S")
            
            time_since_heartbeat = (datetime.now() - last_heartbeat).total_seconds()
            report.append(f"Last Heartbeat: {last_heartbeat_str} (UTC)")
            report.append(f"Time Since Last Heartbeat: {time_since_heartbeat:.2f} seconds")

            if time_since_heartbeat > STALENESS_THRESHOLD_SECONDS:
                is_stale = True
                report.append(f"DIAGNOSIS: The '{service_name}' heartbeat is STALE (exceeds {STALENESS_THRESHOLD_SECONDS}s).")
            else:
                report.append(f"DIAGNOSIS: The '{service_name}' heartbeat is HEALTHY (within {STALENESS_THRESHOLD_SECONDS}s).")

        except ValueError:
            report.append(f"ERROR: Could not parse 'last_heartbeat' timestamp: '{last_heartbeat_str}'.")
            report.append("  Potential Cause: Invalid timestamp format in 'service_health' table.")
            report.append("  Recommended Next Steps: Verify 'gate_orchestrator' is sending heartbeats in a valid ISO format.")
            is_stale = True # Treat as stale if heartbeat cannot be parsed
    else:
        report.append("ERROR: 'last_heartbeat' field is missing from health data.")
        report.append("  Potential Cause: 'gate_orchestrator' is not reporting heartbeats, or the 'service_health' schema is incorrect.")
        is_stale = True # Treat as stale if heartbeat is missing

    report.append(f"Observed Status: {status}")
    report.append(f"Observed PID: {pid if pid else 'N/A'}")
    report.append("-" * 60)

    # --- Analyze Potential Causes and Recommend Next Steps ---
    report.append("Potential Causes and Recommended Next Steps:")

    if not is_stale and status == "RUNNING":
        report.append(f"  The '{service_name}' appears to be running and reporting health normally.")
        report.append("  If you suspect an issue, consider checking its specific application logs for errors or warnings.")
    elif status == "STOPPED" or status == "CRASHED":
        report.append(f"  The '{service_name}' is reported as '{status}'.")
        report.append("  Potential Cause: The daemon has intentionally stopped or crashed unexpectedly.")
        report.append("  Recommended Next Steps:")
        report.append(f"    1. Check system logs (e.g., `journalctl -u {service_name}.service` or `/var/log/syslog`) for crash reports or shutdown messages.")
        report.append(f"    2. Inspect the '{service_name}' application-specific logs for error messages leading up to the stop/crash.")
        report.append(f"    3. Attempt to restart the '{service_name}' daemon and monitor its health.")
    elif status == "RUNNING" and is_stale:
        report.append(f"  The '{service_name}' is reported as 'RUNNING' but its heartbeat is stale.")
        report.append("  Potential Causes:")
        report.append("    - The process is hung, deadlocked, or in an infinite loop, preventing it from updating its heartbeat.")
        report.append("    - Resource contention (CPU, memory, disk I/O) is preventing the process from executing normally.")
        report.append("    - The daemon is experiencing internal errors that prevent heartbeat updates but don't cause a full crash.")
        report.append("    - The system clock on the machine running 'gate_orchestrator' is significantly out of sync.")
        report.append("  Recommended Next Steps:")
        report.append(f"    1. Check the '{service_name}' application-specific logs for recent errors, warnings, or unusual activity.")
        report.append(f"    2. Monitor system resources (CPU, memory, disk, network) on the host running '{service_name}' for contention.")
        report.append(f"    3. If available, use system tools (e.g., `top`, `htop`, `strace`, `gdb`) to inspect the process with PID {pid} (if valid) for activity.")
        report.append(f"    4. Verify system clock synchronization on the host running '{service_name}'.")
        report.append(f"    5. Consider restarting the '{service_name}' daemon if logs don't provide immediate answers.")
    elif status == "UNKNOWN" or (status == "RUNNING" and pid is None):
        report.append(f"  The '{service_name}' status is '{status}' or PID is missing/invalid.")
        report.append("  Potential Cause: The daemon might not be running, or its health reporting is inconsistent.")
        report.append("  Recommended Next Steps:")
        report.append(f"    1. Manually check if the '{service_name}' process is running on its host (e.g., `ps aux | grep gate_orchestrator`).")
        report.append(f"    2. Inspect system logs for startup failures or unexpected terminations.")
        report.append(f"    3. Ensure the '{service_name}' daemon is correctly configured to report its PID and status.")
    else:
        report.append(f"  Unusual status '{status}' detected. Further investigation is required.")
        report.append("  Recommended Next Steps:")
        report.append(f"    1. Consult '{service_name}' documentation for expected status values.")
        report.append(f"    2. Review '{service_name}' application logs for context.")

    report.append("-" * 60)
    report.append("End of Report.")

    print("\n".join(report))

if __name__ == "__main__":
    run()