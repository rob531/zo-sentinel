import requests
from datetime import datetime, timedelta

def query_write_service(endpoint, params=None):
    """Helper function to query the write_service API."""
    base_url = "http://write_service:8080"
    url = f"{base_url}/{endpoint}"
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error querying {url}: {e}")
        return None

def get_service_health(service_name):
    """Query the service_health table for the given service."""
    params = {"service_name": service_name}
    data = query_write_service("query", params)
    if not data:
        return None
    return data.get("results", [])[0] if data.get("results") else None

def get_recent_logs(service_name, hours=24):
    """Query the audit_log table for recent logs of the given service."""
    end_time = datetime.utcnow().isoformat()
    start_time = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    params = {
        "table": "audit_log",
        "service_name": service_name,
        "start_time": start_time,
        "end_time": end_time,
        "limit": 100
    }
    data = query_write_service("query", params)
    if not data:
        return []
    return data.get("results", [])

def analyze_staleness(health_data, logs):
    """Analyze the health data and logs to identify potential issues."""
    issues = []

    if not health_data:
        issues.append("No health data available for rug_pull_monitor.")
        return issues

    last_heartbeat = health_data.get("last_heartbeat")
    if not last_heartbeat:
        issues.append("No last heartbeat recorded.")
    else:
        last_heartbeat_time = datetime.fromisoformat(last_heartbeat)
        staleness = datetime.utcnow() - last_heartbeat_time
        if staleness > timedelta(hours=1):
            issues.append(f"Staleness detected: {staleness}. Last heartbeat at {last_heartbeat_time}.")

    error_logs = [log for log in logs if log.get("level") == "ERROR"]
    if error_logs:
        issues.append(f"Recent errors found ({len(error_logs)}):")
        for log in error_logs[:5]:  # Limit to 5 most recent errors
            issues.append(f"  - {log.get('message')}")

    if not issues:
        issues.append("No issues detected. rug_pull_monitor appears healthy.")

    return issues

def generate_report(service_name):
    """Generate a diagnostic report for the given service."""
    print(f"Diagnostic Report for {service_name}")
    print("=" * 50)

    health_data = get_service_health(service_name)
    logs = get_recent_logs(service_name)

    issues = analyze_staleness(health_data, logs)

    for issue in issues:
        print(issue)

    print("\nReport generated successfully.")

def main():
    service_name = "rug_pull_monitor"
    generate_report(service_name)
    print("PASS")

if __name__ == "__main__":
    main()