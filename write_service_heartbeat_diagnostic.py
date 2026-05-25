import requests
from datetime import datetime, timezone

def diagnose_write_service_heartbeat_staleness():
    url = 'http://127.0.0.1:8772/write'
    headers = {'Content-Type': 'application/json'}
    data = {
        "table": "service_health",
        "rows": {
            "service": "write_service", 
            "last_heartbeat": datetime.now(timezone.utc).isoformat() + 'Z',
            "meta": {}
        },
        "wait": True
    }
    
    try:
        response = requests.post(url, json=data, headers=headers)
        if not response.status_code == 200:
            return f"Failed to get data from {url}, status code: {response.status_code}"
        
        timestamp = datetime.now(timezone.utc).isoformat() + 'Z'
        
        write_service_heartbeat = requests.get(f'http://127.0.0.1:8772/write?timestamp={timestamp}')
        if not write_service_heartbeat.status_code == 200:
            return f"Failed to get write service heartbeat, status code: {write_service_heartbeat.status_code}"
        else:
            return "Write service is responsive"
    except requests.exceptions.RequestException as e:
        return f"Request Exception: {e}"