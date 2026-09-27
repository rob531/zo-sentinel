import requests
import time
from datetime import datetime
from typing import Optional

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_NAME = "write_service_health_probe"
HEARTBEAT_INTERVAL = 60  # seconds

def ping_write_service() -> bool:
    """Ping the write_service /query endpoint with a 10s timeout."""
    try:
        response = requests.get(
            f"{WRITE_SERVICE_URL}/query",
            timeout=10
        )
        return response.status_code == 200
    except requests.RequestException:
        return False

def run():
    """Main daemon loop that pings write_service and writes heartbeat rows."""
    while True:
        write_service_responsive = ping_write_service()
        status = "ok" if write_service_responsive else "degraded"

        # Prepare the heartbeat payload
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "status": status,
                "last_heartbeat": datetime.utcnow().isoformat() + "Z",
                "meta": {
                    "write_service_responsive": write_service_responsive
                }
            }
        }

        # Post the heartbeat to write_service
        try:
            requests.post(
                f"{WRITE_SERVICE_URL}/write",
                json=payload,
                timeout=10
            )
        except requests.RequestException:
            pass  # Silently fail if write_service is unresponsive

        time.sleep(HEARTBEAT_INTERVAL)

if __name__ == "__main__":
    # Self-test: ping_write_service() should return a bool
    try:
        result = ping_write_service()
        assert isinstance(result, bool)
        print("PASS")
    except Exception:
        print("FAIL")