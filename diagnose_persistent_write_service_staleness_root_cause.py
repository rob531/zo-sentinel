import requests
import json
import time

# --- Configuration ---
HEALTH_CHECK_URL = "http://127.0.0.1:8772/service_health"
WRITE_URL = "http://127.0.0.1:8772/write"
SERVICE_NAME = "write_service"
REQUEST_TIMEOUT = 5  # seconds for HTTP requests

# --- Simulated Log Data ---
# In a real scenario, this would read from actual log files (e.g., /var/log/syslog, journalctl, specific service logs)
# For this simulation, we provide a list of log entries.
SIMULATED_LOGS = [
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO {SERVICE_NAME}: Service started successfully on port 8772.",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO {SERVICE_NAME}: Heartbeat OK.",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR {SERVICE_NAME}: Failed to bind to port 8772: Address already in use.",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} WARNING {SERVICE_NAME}: High memory usage detected (95%).",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR {SERVICE_NAME}: Unhandled exception in main loop: KeyError('missing_config_item')",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO {SERVICE_NAME}: Processing incoming request.",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR {SERVICE_NAME}: Database connection lost, retrying...",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} CRITICAL {SERVICE_NAME}: Heartbeat failure detected for 3 consecutive checks.",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO {SERVICE_NAME}: Write operation successful.",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} ERROR {SERVICE_NAME}: Disk full error during write operation.",
    f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO other_service: Another service is running fine.",
]

# --- Diagnostic Functions ---

def get_service_health(url: str, service_name: str, timeout: int) -> dict:
    """
    Queries the service health endpoint for a specific service.
    """
    print(f"Attempting to query service health for '{service_name}' at {url}...")
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
        health_data = response.json()

        if service_name in health_data:
            service_info = health_data[service_name]
            return {
                "status": service_info.get("status", "UNKNOWN"),
                "meta": service_info.get("meta", {}),
                "error": None,
                "http_status": response.status_code
            }
        else:
            return {
                "status": "NOT_FOUND",
                "meta": {},
                "error": f"Service '{service_name}' not found in health response.",
                "http_status": response.status_code
            }
    except requests.exceptions.ConnectionError:
        return {
            "status": "UNREACHABLE",
            "meta": {},
            "error": f"Connection error: Could not connect to {url}. Is the service running?",
            "http_status": None
        }
    except requests.exceptions.Timeout:
        return {
            "status": "TIMEOUT",
            "meta": {},
            "error": f"Request timed out after {timeout} seconds while connecting to {url}.",
            "http_status": None
        }
    except requests.exceptions.HTTPError as e:
        return {
            "status": "HTTP_ERROR",
            "meta": {},
            "error": f"HTTP error {e.response.status_code}: {e.response.reason}",
            "http_status": e.response.status_code
        }
    except json.JSONDecodeError:
        return {
            "status": "INVALID_RESPONSE",
            "meta": {},
            "error": f"Failed to decode JSON response from {url}. Response: {response.text[:200]}...",
            "http_status": response.status_code if 'response' in locals() else None
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "meta": {},
            "error": f"An unexpected error occurred: {e}",
            "http_status": None
        }

def simulate_log_analysis(logs: list, service_name: str) -> list:
    """
    Analyzes simulated logs for errors or exceptions related to the service.
    """
    print(f"\nAnalyzing simulated logs for '{service_name}' related issues...")
    anomalies = []
    keywords = {
        "error": "ERROR",
        "exception": "EXCEPTION",
        "failed to bind": "PORT_CONFLICT",
        "address already in use": "PORT_CONFLICT",
        "heartbeat failure": "HEARTBEAT_FAILURE",
        "high memory usage": "RESOURCE_EXHAUSTION",
        "disk full": "RESOURCE_EXHAUSTION",
        "database connection lost": "EXTERNAL_DEPENDENCY_ISSUE"
    }

    for entry in logs:
        if service_name in entry:
            for keyword, anomaly_type in keywords.items():
                if keyword in entry.lower():
                    anomalies.append({"type": anomaly_type, "log_entry": entry.strip()})
    return anomalies

def simulate_write_operation(url: str, timeout: int) -> dict:
    """
    Simulates a small write operation to check connectivity and responsiveness.
    """
    print(f"\nAttempting to simulate a write operation to {url}...")
    test_data = {"key": "test_data", "value": f"timestamp_{time.time()}"}
    try:
        response = requests.post(url, json=test_data, timeout=timeout)
        response.raise_for_status()
        return {
            "success": True,
            "http_status": response.status_code,
            "response_text": response.text,
            "error": None
        }
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "http_status": None,
            "response_text": None,
            "error": f"Connection error: Could not connect to {url}. Is the service running and listening on this port?"
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "http_status": None,
            "response_text": None,
            "error": f"Request timed out after {timeout} seconds during write operation to {url}."
        }
    except requests.exceptions.HTTPError as e:
        return {
            "success": False,
            "http_status": e.response.status_code,
            "response_text": e.response.text,
            "error": f"HTTP error {e.response.status_code}: {e.response.reason}"
        }
    except Exception as e:
        return {
            "success": False,
            "http_status": None,
            "response_text": None,
            "error": f"An unexpected error occurred during write operation: {e}"
        }

# --- Main Diagnostic Script ---

def diagnose_persistent_write_service_staleness():
    """
    Main function to diagnose persistent staleness of the write_service daemon.
    """
    print("--- Diagnosing Write Service Staleness ---")
    report = {}

    # 1. Query service_health
    health_report = get_service_health(HEALTH_CHECK_URL, SERVICE_NAME, REQUEST_TIMEOUT)
    report["service_health"] = health_report

    # 2. Analyze simulated logs
    log_anomalies = simulate_log_analysis(SIMULATED_LOGS, SERVICE_NAME)
    report["log_analysis"] = log_anomalies

    # 3. Simulate a write operation
    write_op_report = simulate_write_operation(WRITE_URL, REQUEST_TIMEOUT)
    report["write_operation"] = write_op_report

    # --- Generate Detailed Report and Potential Root Causes ---
    print("\n--- Diagnostic Summary ---")
    print(f"Service Name: {SERVICE_NAME}")
    print(f"Health Check URL: {HEALTH_CHECK_URL}")
    print(f"Write Operation URL: {WRITE_URL}")
    print(f"Request Timeout: {REQUEST_TIMEOUT}s")

    print("\n--- 1. Service Health Status ---")
    print(f"  Status: {report['service_health']['status']}")
    print(f"  HTTP Status: {report['service_health']['http_status']}")
    if report['service_health']['error']:
        print(f"  Error: {report['service_health']['error']}")
    if report['service_health']['meta']:
        print("  Meta Information:")
        print(json.dumps(report['service_health']['meta'], indent=2))

    print("\n--- 2. Simulated Write Operation ---")
    print(f"  Success: {report['write_operation']['success']}")
    print(f"  HTTP Status: {report['write_operation']['http_status']}")
    if report['write_operation']['error']:
        print(f"  Error: {report['write_operation']['error']}")
    else:
        print(f"  Response (partial): {report['write_operation']['response_text'][:100]}...")

    print("\n--- 3. Log Analysis Findings ---")
    if report['log_analysis']:
        for anomaly in report['log_analysis']:
            print(f"  - Type: {anomaly['type']}, Log: {anomaly['log_entry']}")
    else:
        print("  No specific anomalies found in simulated logs for this service.")

    print("\n--- Potential Root Causes & Recommendations ---")
    potential_causes = []

    # Evaluate health check and write operation
    if report['service_health']['status'] in ["UNREACHABLE", "TIMEOUT", "HTTP_ERROR", "NOT_FOUND"]:
        potential_causes.append("The `write_service` daemon appears to be down, unresponsive, or not exposing its health endpoint correctly.")
        if report['write_operation']['success'] is False and report['write_operation']['error'] and "Connection error" in report['write_operation']['error']:
            potential_causes.append("Both health check and write operation failed with connection errors, strongly suggesting the service is not running or not listening on port 8772.")
            if any(a['type'] == "PORT_CONFLICT" for a in report['log_analysis']):
                potential_causes.append("Log analysis indicates a **Port Conflict (8772)**: Another process might be using the required port, preventing `write_service` from starting.")
            else:
                potential_causes.append("Consider checking if the `write_service` process is running (`ps aux | grep write_service`) and if port 8772 is open and listening (`netstat -tulnp | grep 8772`).")
        elif report['service_health']['status'] == "HTTP_ERROR" and report['service_health']['http_status'] == 500:
            potential_causes.append("The health endpoint returned a server error (500), indicating an internal issue within the service even if it's running.")

    elif report['service_health']['status'] == "UP" and report['write_operation']['success'] is False:
        potential_causes.append("The `write_service` reports 'UP' via health check, but the write operation failed. This could indicate a specific issue with the `/write` endpoint or an intermittent problem.")
        if any(a['type'] == "EXTERNAL_DEPENDENCY_ISSUE" for a in report['log_analysis']):
            potential_causes.append("Log analysis suggests an **External Dependency Issue** (e.g., database connection lost) affecting write operations.")
        if any(a['type'] == "RESOURCE_EXHAUSTION" for a in report['log_analysis']):
            potential_causes.append("Log analysis indicates **Resource Exhaustion** (e.g., disk full, high memory) which might prevent write operations despite the service being 'UP'.")

    elif report['service_health']['status'] == "STALE" or report['service_health']['status'] == "DEGRADED":
        potential_causes.append(f"The `write_service` health status is '{report['service_health']['status']}', indicating it's not fully functional or responsive.")
        if any(a['type'] == "HEARTBEAT_FAILURE" for a in report['log_analysis']):
            potential_causes.append("Log analysis shows **Heartbeat Failures**, suggesting the service is struggling to maintain its operational state.")

    # Evaluate log analysis findings
    if any(a['type'] == "EXCEPTION" for a in report['log_analysis']):
        potential_causes.append("Log analysis detected **Unhandled Exceptions**: A software bug or unexpected condition is causing the service to crash or behave erratically.")
    if any(a['type'] == "ERROR" for a in report['log_analysis']) and not any(a['type'] in ["PORT_CONFLICT", "HEARTBEAT_FAILURE", "RESOURCE_EXHAUSTION", "EXTERNAL_DEPENDENCY_ISSUE"] for a in report['log_analysis']):
        potential_causes.append("Generic errors were found in logs. Further investigation of specific error messages is needed to pinpoint the exact issue.")

    if not potential_causes:
        potential_causes.append("No obvious anomalies detected by this script. The service appears to be running and responsive based on current checks and simulated logs. If staleness persists, consider deeper monitoring or more detailed log analysis.")

    for i, cause in enumerate(potential_causes):
        print(f"  {i+1}. {cause}")

    print("\n--- End of Diagnosis ---")

if __name__ == "__main__":
    # To simulate different scenarios, you can modify SIMULATED_LOGS or
    # temporarily change HEALTH_CHECK_URL/WRITE_URL to non-existent addresses
    # to test connection errors.
    # Example:
    # HEALTH_CHECK_URL = "http://127.0.0.1:9999/service_health" # To simulate connection error
    # SIMULATED_LOGS = [f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO {SERVICE_NAME}: Service started successfully on port 8772."] # To simulate no errors

    diagnose_persistent_write_service_staleness()