import requests
import subprocess
import datetime
import re

def query_service_health():
    url = "http://write_service/query"
    query = "SELECT last_heartbeat FROM service_health WHERE service_name = 'zo_sentinel_builder'"
    payload = {'query': query}
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        return response.json()
    else:
        return None

def check_builder_process():
    try:
        output = subprocess.check_output(['ps', 'aux']).decode('utf-8')
        if 'zo_sentinel_builder' in output:
            return True
        else:
            return False
    except subprocess.CalledProcessError:
        return False

def check_system_logs():
    try:
        output = subprocess.check_output(['journalctl', '-u', 'zo_sentinel_builder', '--no-pager']).decode('utf-8')
        error_pattern = re.compile(r'error|exception|fail', re.IGNORECASE)
        if error_pattern.search(output):
            return True
        else:
            return False
    except subprocess.CalledProcessError:
        return False

def diagnose_staleness():
    findings = []

    # Check last heartbeat
    service_health_data = query_service_health()
    if service_health_data:
        last_heartbeat = service_health_data['last_heartbeat']
        current_time = datetime.datetime.now()
        time_diff = current_time - datetime.datetime.fromisoformat(last_heartbeat)
        if time_diff.total_seconds() > 300:  # 5 minutes
            findings.append(f"Last heartbeat was {time_diff.total_seconds()} seconds ago, which is more than 5 minutes.")
        else:
            findings.append("Last heartbeat was within the last 5 minutes.")
    else:
        findings.append("Could not query the service_health table.")

    # Check builder process
    if check_builder_process():
        findings.append("The zo_sentinel_builder process is running.")
    else:
        findings.append("The zo_sentinel_builder process is not running.")

    # Check system logs
    if check_system_logs():
        findings.append("There are builder-related errors in the system logs.")
    else:
        findings.append("No builder-related errors found in the system logs.")

    # Determine root cause
    if "Last heartbeat was within the last 5 minutes." in findings and "The zo_sentinel_builder process is running." in findings and "No builder-related errors found in the system logs." in findings:
        return "no clear cause"
    else:
        return " ".join(findings)

if __name__ == "__main__":
    status_message = diagnose_staleness()
    print("Diagnostic Findings:")
    print(status_message)
    assert status_message, "No status message returned"