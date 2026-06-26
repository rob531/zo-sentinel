import requests
import json
from datetime import datetime, timedelta

def query_service_health():
    url = "http://localhost:5000/write_service"
    query = {
        "table": "service_health",
        "service": "self_diagnostics",
        "limit": 1
    }
    response = requests.post(url, json=query)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to query service_health: {response.text}")

def parse_meta(meta_json):
    try:
        meta = json.loads(meta_json)
        return meta
    except json.JSONDecodeError:
        raise Exception("Failed to parse meta JSON")

def check_diagnostic_data(meta):
    required_keys = ["last_scan_time", "issues_found"]
    for key in required_keys:
        if key not in meta:
            raise Exception(f"Missing required key in meta: {key}")

    last_scan_time = datetime.strptime(meta["last_scan_time"], "%Y-%m-%d %H:%M:%S")
    if last_scan_time < datetime.now() - timedelta(minutes=5):
        raise Exception("last_scan_time is too old")

    if not isinstance(meta["issues_found"], int):
        raise Exception("issues_found is not an integer")

def main():
    try:
        service_health_data = query_service_health()
        if not service_health_data:
            raise Exception("No service_health data found")

        meta = parse_meta(service_health_data[0]["meta"])
        check_diagnostic_data(meta)

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")

if __name__ == "__main__":
    main()