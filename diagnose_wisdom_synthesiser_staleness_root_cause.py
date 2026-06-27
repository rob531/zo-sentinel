import json
from datetime import datetime, timedelta

# Mock database and logging utilities
class MockWriteService:
    def __init__(self):
        self.service_health_data = {
            "wisdom_synthesiser": {
                "last_heartbeat": datetime.now() - timedelta(hours=2),
                "meta": {"status": "stale", "version": "1.0.0"}
            }
        }
        self.log_entries = [
            {"timestamp": datetime.now() - timedelta(minutes=30), "level": "ERROR", "message": "Unhandled exception in processing loop"},
            {"timestamp": datetime.now() - timedelta(minutes=15), "level": "WARN", "message": "High CPU usage detected"},
            {"timestamp": datetime.now() - timedelta(minutes=5), "level": "INFO", "message": "Processing batch 123"}
        ]

    def query_service_health(self, service_name):
        return self.service_health_data.get(service_name, {})

    def query_logs(self, service_name, limit=100):
        return [log for log in self.log_entries if service_name in log.get("message", "")]

class MockSystemResourceChecker:
    def __init__(self):
        self.resource_usage = {
            "cpu": 95,
            "memory": 80,
            "io_wait": 10
        }

    def get_resource_usage(self):
        return self.resource_usage

def diagnose_wisdom_synthesiser_staleness():
    write_service = MockWriteService()
    resource_checker = MockSystemResourceChecker()

    # Step 1: Query service health
    health_data = write_service.query_service_health("wisdom_synthesiser")
    last_heartbeat = health_data.get("last_heartbeat")
    current_time = datetime.now()
    staleness_duration = current_time - last_heartbeat

    # Step 2: Analyze logs
    logs = write_service.query_logs("wisdom_synthesiser")
    error_patterns = ["Unhandled exception", "Error", "Failed"]
    log_errors = [log for log in logs if any(pattern in log["message"] for pattern in error_patterns)]

    # Step 3: Check resource contention
    resource_usage = resource_checker.get_resource_usage()
    high_cpu = resource_usage["cpu"] > 90
    high_memory = resource_usage["memory"] > 85
    high_io_wait = resource_usage["io_wait"] > 5

    # Generate diagnostic report
    report = {
        "service": "wisdom_synthesiser",
        "staleness_duration": str(staleness_duration),
        "potential_causes": []
    }

    if log_errors:
        report["potential_causes"].append({
            "cause": "Unhandled exceptions or errors in logs",
            "details": [log["message"] for log in log_errors]
        })

    if high_cpu or high_memory or high_io_wait:
        report["potential_causes"].append({
            "cause": "Resource contention",
            "details": {
                "high_cpu": high_cpu,
                "high_memory": high_memory,
                "high_io_wait": high_io_wait
            }
        })

    if not report["potential_causes"]:
        report["potential_causes"].append({
            "cause": "Unknown",
            "details": "No obvious causes found in logs or resource usage"
        })

    return report

if __name__ == "__main__":
    diagnostic_report = diagnose_wisdom_synthesiser_staleness()
    print(json.dumps(diagnostic_report, indent=2))

    # Assert that a plausible root cause is identified
    assert any(cause["cause"] == "Unhandled exceptions or errors in logs" for cause in diagnostic_report["potential_causes"]), "PASS"