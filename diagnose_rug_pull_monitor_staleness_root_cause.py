import requests
from datetime import datetime, timedelta

def query_service_health(service_name, hours=24):
    """Query the service_health table for recent heartbeats."""
    url = "http://internal-db-service/query"
    query = f"""
    SELECT timestamp, status, details
    FROM service_health
    WHERE service_name = '{service_name}'
    AND timestamp > NOW() - INTERVAL '{hours} HOUR'
    ORDER BY timestamp DESC
    """
    payload = {"query": query}
    response = requests.post(url, json=payload)
    if response.status_code != 200:
        raise Exception(f"Query failed: {response.text}")
    return response.json()["results"]

def check_system_logs(service_name, hours=24):
    """Check system logs for errors related to the service."""
    url = "http://internal-log-service/search"
    payload = {
        "query": f"service:{service_name} AND level:ERROR",
        "time_range": f"last {hours}h"
    }
    response = requests.post(url, json=payload)
    if response.status_code != 200:
        raise Exception(f"Log search failed: {response.text}")
    return response.json()["logs"]

def analyze_staleness(heartbeats, logs):
    """Analyze heartbeats and logs to identify potential causes of staleness."""
    report = {
        "service": "rug_pull_monitor",
        "last_heartbeat": None,
        "staleness_duration": None,
        "potential_causes": []
    }

    if not heartbeats:
        report["potential_causes"].append("No recent heartbeats found in the database.")
        return report

    last_heartbeat = heartbeats[0]
    report["last_heartbeat"] = last_heartbeat["timestamp"]
    last_heartbeat_time = datetime.fromisoformat(last_heartbeat["timestamp"].replace("Z", "+00:00"))
    staleness_duration = datetime.utcnow() - last_heartbeat_time
    report["staleness_duration"] = str(staleness_duration)

    if last_heartbeat["status"] != "OK":
        report["potential_causes"].append(f"Last heartbeat status was not OK: {last_heartbeat['status']}")

    if "details" in last_heartbeat and last_heartbeat["details"]:
        report["potential_causes"].append(f"Last heartbeat details: {last_heartbeat['details']}")

    if logs:
        report["potential_causes"].append("Errors found in system logs:")
        for log in logs[:5]:  # Limit to 5 logs to avoid overwhelming output
            report["potential_causes"].append(f"- {log['message']}")

    # Common causes of daemon staleness
    common_causes = [
        "Unhandled exceptions in rug_pull_monitor.py",
        "Infinite loops in rug_pull_monitor.py",
        "External service dependencies (e.g., API timeouts)",
        "Resource exhaustion (e.g., memory leaks)",
        "Network connectivity issues"
    ]
    report["potential_causes"].extend(common_causes)

    return report

def generate_report(report):
    """Generate a detailed report of findings."""
    print("=" * 80)
    print("Rug Pull Monitor Staleness Diagnostic Report")
    print("=" * 80)
    print(f"Service: {report['service']}")
    print(f"Last Heartbeat: {report['last_heartbeat']}")
    print(f"Staleness Duration: {report['staleness_duration']}")
    print("\nPotential Causes:")
    for cause in report["potential_causes"]:
        print(f"- {cause}")
    print("=" * 80)

if __name__ == "__main__":
    try:
        heartbeats = query_service_health("rug_pull_monitor", hours=168)  # 7 days to cover 163h11m
        logs = check_system_logs("rug_pull_monitor", hours=168)
        report = analyze_staleness(heartbeats, logs)
        generate_report(report)
        print("PASS: Report generated successfully.")
    except Exception as e:
        print(f"ERROR: {str(e)}")