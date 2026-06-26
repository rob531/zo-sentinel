import requests
import time
from datetime import datetime, timedelta

def query_service_health():
    url = "http://localhost:8080/service_health"
    payload = {
        "service": "anti_entropy",
        "limit": 10
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def query_data_tables():
    tables = ["mcp_server_registry", "mcp_signal_scores"]
    results = {}
    for table in tables:
        url = f"http://localhost:8080/query/{table}"
        payload = {
            "query": f"SELECT updated_at FROM {table} ORDER BY updated_at DESC LIMIT 1"
        }
        response = requests.post(url, json=payload)
        response.raise_for_status()
        results[table] = response.json()
    return results

def check_recent_activity(data_tables_results):
    threshold = datetime.now() - timedelta(minutes=5)
    for table, result in data_tables_results.items():
        if not result or result[0]["updated_at"] < threshold.isoformat():
            return False
    return True

def check_heartbeat(service_health_results):
    if not service_health_results:
        return False
    for entry in service_health_results:
        if entry["status"] != "healthy":
            return False
    return True

def main():
    try:
        service_health_results = query_service_health()
        data_tables_results = query_data_tables()

        if check_recent_activity(data_tables_results) and check_heartbeat(service_health_results):
            print("PASS")
        else:
            print("FAIL")
    except Exception as e:
        print(f"FAIL: {e}")

if __name__ == "__main__":
    main()