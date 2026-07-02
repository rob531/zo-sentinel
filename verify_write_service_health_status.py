import requests
from datetime import datetime, timedelta
from app.db import get_session
from app.models import ServiceHealth
from fastapi import Depends

def verify_write_service_health():
    session = next(get_session())
    try:
        # Test write request
        write_data = {"key": "test_key", "value": "test_value"}
        write_response = requests.post("http://127.0.0.1:8772/write", json=write_data, timeout=5)
        write_response.raise_for_status()

        # Test query request
        query_data = {"key": "test_key"}
        query_response = requests.post("http://127.0.0.1:8772/query", json=query_data, timeout=5)
        query_response.raise_for_status()

        # Verify heartbeat
        service_health = session.query(ServiceHealth).filter_by(service_name="write_service").first()
        if not service_health or service_health.last_heartbeat < datetime.utcnow() - timedelta(seconds=10):
            raise Exception("Heartbeat not updated")

        # Check for error logs
        error_logs = session.execute("SELECT * FROM audit_log WHERE service_name = 'write_service' AND log_level = 'ERROR'").fetchall()
        if error_logs:
            raise Exception("Error logs found")

        print("PASS: write_service is healthy")
        return True
    except Exception as e:
        print(f"FAIL: write_service is unhealthy - {str(e)}")
        return False
    finally:
        session.close()

if __name__ == "__main__":
    verify_write_service_health()