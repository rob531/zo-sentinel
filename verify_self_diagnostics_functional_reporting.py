import requests
import json
from typing import Dict, Any

def query_service_health() -> Dict[str, Any]:
    """Query the service_health table for self_diagnostics entries."""
    url = "http://localhost:8000/query"
    query = """
    SELECT meta, status
    FROM service_health
    WHERE service_name = 'self_diagnostics'
    ORDER BY timestamp DESC
    LIMIT 1
    """
    payload = {"query": query}
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()["data"][0]

def verify_diagnostic_report(report: Dict[str, Any]) -> bool:
    """Verify that the diagnostic report contains meaningful data."""
    if not report:
        return False

    # Check for expected keys in the meta or status
    meta = report.get("meta", {})
    status = report.get("status", {})

    # Example checks (adjust based on actual expected keys/patterns)
    required_keys = ["system_load", "memory_usage", "disk_usage", "network_status"]
    has_required_keys = all(key in meta for key in required_keys)

    # Check for non-empty values
    has_non_empty_values = all(
        isinstance(meta.get(key), (int, float, str)) and meta.get(key) is not None
        for key in required_keys
    )

    return has_required_keys and has_non_empty_values

def main():
    try:
        report = query_service_health()
        if verify_diagnostic_report(report):
            print("PASS")
        else:
            print("FAIL: Diagnostic report does not contain expected data")
    except Exception as e:
        print(f"FAIL: {str(e)}")

if __name__ == "__main__":
    main()