import requests
from datetime import datetime, timedelta
import time

class GateSchedulerDiagnoser:
    def __init__(self, write_service_url):
        self.write_service_url = write_service_url

    def query_service_health(self):
        try:
            response = requests.get(f"{self.write_service_url}/service_health")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": f"Failed to query service_health: {str(e)}"}

    def analyze_gate_scheduler(self, service_health_data):
        if "error" in service_health_data:
            return service_health_data["error"]

        gate_scheduler_entries = [
            entry for entry in service_health_data
            if entry.get("service_name") == "gate_scheduler"
        ]

        if not gate_scheduler_entries:
            return "No gate_scheduler entries found in service_health table."

        for entry in gate_scheduler_entries:
            last_heartbeat = entry.get("last_heartbeat")
            status = entry.get("status")

            if not last_heartbeat:
                return "gate_scheduler entry has no last_heartbeat timestamp."

            last_heartbeat_time = datetime.fromisoformat(last_heartbeat)
            current_time = datetime.utcnow()
            staleness_threshold = timedelta(minutes=5)

            if current_time - last_heartbeat_time > staleness_threshold:
                if status == "running":
                    return (
                        "gate_scheduler is stale (last heartbeat > 5 minutes ago) "
                        "but status is 'running'. Possible causes:\n"
                        "- Process is hung or crashed\n"
                        "- Database connectivity issues\n"
                        "- Long-running task preventing heartbeats"
                    )
                elif status == "stopped":
                    return (
                        "gate_scheduler is stale and status is 'stopped'. "
                        "Possible causes:\n"
                        "- Process was intentionally stopped\n"
                        "- Process crashed and wasn't restarted"
                    )
                else:
                    return (
                        f"gate_scheduler is stale with unknown status '{status}'. "
                        "Check service logs for more information."
                    )

        return "gate_scheduler appears healthy (recent heartbeat and valid status)."

def mock_service_health_data(stale=True):
    if stale:
        return [
            {
                "service_name": "gate_scheduler",
                "last_heartbeat": (datetime.utcnow() - timedelta(minutes=10)).isoformat(),
                "status": "running"
            }
        ]
    else:
        return [
            {
                "service_name": "gate_scheduler",
                "last_heartbeat": datetime.utcnow().isoformat(),
                "status": "running"
            }
        ]

def test_diagnoser():
    # Test with stale data
    diagnoser = GateSchedulerDiagnoser("http://mock-write-service")
    stale_data = mock_service_health_data(stale=True)
    result = diagnoser.analyze_gate_scheduler(stale_data)
    assert "gate_scheduler is stale" in result
    assert "Process is hung or crashed" in result or "Database connectivity issues" in result or "Long-running task" in result

    # Test with healthy data
    healthy_data = mock_service_health_data(stale=False)
    result = diagnoser.analyze_gate_scheduler(healthy_data)
    assert "gate_scheduler appears healthy" in result

    print("All tests passed!")

if __name__ == "__main__":
    # For testing purposes
    test_diagnoser()

    # For real usage (uncomment when ready)
    # diagnoser = GateSchedulerDiagnoser("http://your-write-service-url")
    # service_health_data = diagnoser.query_service_health()
    # print(diagnoser.analyze_gate_scheduler(service_health_data))