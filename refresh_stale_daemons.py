import json
from datetime import datetime, timedelta

# Sample data for demonstration (in a real scenario, this would come from service_health)
service_health_data = {
    "write_service": {
        "last_successful_operation": "2023-10-01T12:00:00Z",
        "expected_heartbeat_interval": "1h"
    },
    "mcp_scanner": {
        "last_successful_operation": "2023-10-01T18:30:00Z",
        "expected_heartbeat_interval": "1h"
    },
    "rug_pull_monitor": {
        "last_successful_operation": "2023-09-24T12:04:00Z",
        "expected_heartbeat_interval": "1h"
    },
    "anti_entropy": {
        "last_successful_operation": "2023-10-01T18:29:00Z",
        "expected_heartbeat_interval": "1h"
    },
    "wisdom_synthesiser": {
        "last_successful_operation": "2023-10-01T12:01:00Z",
        "expected_heartbeat_interval": "1h"
    }
}

def parse_iso_format(iso_string):
    return datetime.fromisoformat(iso_string.replace('Z', '+00:00'))

def parse_interval(interval_str):
    if interval_str.endswith('h'):
        return timedelta(hours=int(interval_str[:-1]))
    elif interval_str.endswith('m'):
        return timedelta(minutes=int(interval_str[:-1]))
    else:
        return timedelta(hours=1)  # default to 1 hour if format not recognized

def generate_diagnostic_report():
    report = []
    current_time = datetime.utcnow()

    for daemon, data in service_health_data.items():
        last_success = parse_iso_format(data["last_successful_operation"])
        interval = parse_interval(data["expected_heartbeat_interval"])
        age = current_time - last_success

        stale = age > interval
        recommendation = "Restart recommended" if stale else "No action needed"

        report.append({
            "daemon": daemon,
            "last_successful_operation": data["last_successful_operation"],
            "expected_heartbeat_interval": data["expected_heartbeat_interval"],
            "current_age": str(age),
            "stale": stale,
            "recommendation": recommendation
        })

    return json.dumps(report, indent=2)

# Generate and print the diagnostic report
print(generate_diagnostic_report())