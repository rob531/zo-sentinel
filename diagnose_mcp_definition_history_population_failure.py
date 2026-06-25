import requests
import json
from datetime import datetime, timedelta

# Configuration
DB_ACCESS_URL = "http://db-access-service:5000/query"
SERVICE_HEALTH_URL = "http://service-health:5000/heartbeat"
MCP_DEFINITION_HISTORY_POPULATOR_SERVICE = "mcp_definition_history_populator"
LOGS_URL = "http://logs-service:5000/search"

def check_mcp_submissions():
    query = """
    SELECT COUNT(*) as count
    FROM mcp_submissions
    WHERE created_at > NOW() - INTERVAL '1 hour';
    """
    response = requests.post(DB_ACCESS_URL, json={"query": query})
    if response.status_code == 200:
        result = response.json()
        return result[0]['count'] > 0
    else:
        print(f"Error accessing DB: {response.text}")
        return False

def check_daemon_heartbeat():
    response = requests.get(f"{SERVICE_HEALTH_URL}/{MCP_DEFINITION_HISTORY_POPULATOR_SERVICE}")
    if response.status_code == 200:
        heartbeat = response.json()
        last_heartbeat = datetime.strptime(heartbeat['last_heartbeat'], '%Y-%m-%d %H:%M:%S')
        return last_heartbeat > datetime.now() - timedelta(minutes=5)
    else:
        print(f"Error accessing service health: {response.text}")
        return False

def check_logs_for_errors():
    query = {
        "service": MCP_DEFINITION_HISTORY_POPULATOR_SERVICE,
        "level": "ERROR",
        "time_range": "1h"
    }
    response = requests.post(LOGS_URL, json=query)
    if response.status_code == 200:
        logs = response.json()
        return len(logs) == 0
    else:
        print(f"Error accessing logs: {response.text}")
        return False

def check_mcp_server_registry():
    query = """
    SELECT COUNT(*) as count
    FROM mcp_server_registry;
    """
    response = requests.post(DB_ACCESS_URL, json={"query": query})
    if response.status_code == 200:
        result = response.json()
        return result[0]['count'] > 0
    else:
        print(f"Error accessing DB: {response.text}")
        return False

def diagnose():
    findings = []

    # Check for new entries in mcp_submissions
    new_submissions = check_mcp_submissions()
    if not new_submissions:
        findings.append("No new entries found in mcp_submissions in the last hour.")

    # Check daemon heartbeat
    daemon_running = check_daemon_heartbeat()
    if not daemon_running:
        findings.append("MCP definition history populator daemon is not running or not sending heartbeats.")

    # Check logs for errors
    no_errors = check_logs_for_errors()
    if not no_errors:
        findings.append("Errors found in logs for MCP definition history populator service.")

    # Check mcp_server_registry for existing MCPs
    mcp_exists = check_mcp_server_registry()
    if not mcp_exists:
        findings.append("No MCPs found in mcp_server_registry.")

    if not findings:
        findings.append("No issues found. The mcp_definition_history table should be populated.")

    return findings

if __name__ == "__main__":
    findings = diagnose()
    print("Diagnostic Findings:")
    for finding in findings:
        print(f"- {finding}")

    assert findings, "Diagnostic conclusion not provided."