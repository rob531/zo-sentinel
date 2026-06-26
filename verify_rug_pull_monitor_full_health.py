import requests
import time
from datetime import datetime, timedelta

def query_service_health():
    url = "http://write_service/service_health"
    payload = {
        "service_name": "rug_pull_monitor",
        "limit": 10
    }
    response = requests.post(url, json=payload)
    return response.json()

def query_mcp_signal_scores():
    url = "http://write_service/mcp_signal_scores"
    payload = {
        "limit": 10
    }
    response = requests.post(url, json=payload)
    return response.json()

def query_mcp_threat_associations():
    url = "http://write_service/mcp_threat_associations"
    payload = {
        "limit": 10
    }
    response = requests.post(url, json=payload)
    return response.json()

def check_recent_monitoring_records(records, threshold_minutes=5):
    now = datetime.utcnow()
    threshold = now - timedelta(minutes=threshold_minutes)
    recent_records = [record for record in records if datetime.strptime(record['timestamp'], '%Y-%m-%d %H:%M:%S') > threshold]
    return len(recent_records) > 0

def check_heartbeat_consistency(records, threshold_minutes=5):
    timestamps = [datetime.strptime(record['timestamp'], '%Y-%m-%d %H:%M:%S') for record in records]
    timestamps.sort()
    intervals = [(timestamps[i+1] - timestamps[i]).total_seconds() / 60 for i in range(len(timestamps)-1)]
    consistent = all(interval <= threshold_minutes for interval in intervals)
    return consistent

def verify_rug_pull_monitor_full_health():
    service_health_records = query_service_health()
    mcp_signal_scores_records = query_mcp_signal_scores()
    mcp_threat_associations_records = query_mcp_threat_associations()

    recent_service_health = check_recent_monitoring_records(service_health_records)
    recent_mcp_signal_scores = check_recent_monitoring_records(mcp_signal_scores_records)
    recent_mcp_threat_associations = check_recent_monitoring_records(mcp_threat_associations_records)

    heartbeat_consistent = check_heartbeat_consistency(service_health_records)

    if recent_service_health and (recent_mcp_signal_scores or recent_mcp_threat_associations) and heartbeat_consistent:
        print("PASS")
    else:
        print("FAIL")

if __name__ == "__main__":
    verify_rug_pull_monitor_full_health()