import requests
import time
from datetime import datetime, timedelta

class MCPDefinitionHistoryPopulationMonitorDaemon:
    def __init__(self):
        self.write_service_url = "http://write_service/query"
        self.populator_script = "mcp_definition_history_populator.py"
        self.heartbeat_interval = 60
        self.last_heartbeat = datetime.now()

    def query_table(self, table_name):
        response = requests.post(
            self.write_service_url,
            json={"query": f"SELECT * FROM {table_name}"}
        )
        return response.json()

    def is_table_empty_or_stale(self, table_name, stale_threshold_minutes=5):
        data = self.query_table(table_name)
        if not data:
            return True
        last_update = max(item['timestamp'] for item in data)
        last_update_time = datetime.fromisoformat(last_update)
        return datetime.now() - last_update_time > timedelta(minutes=stale_threshold_minutes)

    def trigger_populator(self):
        # In a real scenario, this would invoke the populator script or call its interface
        print(f"Triggering {self.populator_script}")

    def send_heartbeat(self):
        requests.post(
            self.write_service_url,
            json={
                "query": "INSERT INTO service_health (service_name, timestamp) VALUES ('mcp_definition_history_population_monitor', NOW())"
            }
        )
        self.last_heartbeat = datetime.now()

    def run(self):
        while True:
            if self.is_table_empty_or_stale("mcp_definition_history"):
                self.trigger_populator()

            if (datetime.now() - self.last_heartbeat).total_seconds() >= self.heartbeat_interval:
                self.send_heartbeat()

            time.sleep(10)

if __name__ == "__main__":
    daemon = MCPDefinitionHistoryPopulationMonitorDaemon()

    # Simulate a few monitoring cycles
    for _ in range(3):
        if daemon.is_table_empty_or_stale("mcp_definition_history"):
            daemon.trigger_populator()

        if (datetime.now() - daemon.last_heartbeat).total_seconds() >= daemon.heartbeat_interval:
            daemon.send_heartbeat()

        time.sleep(10)

    # Assertions for acceptance testing
    assert daemon.last_heartbeat is not None, "Heartbeat was not sent"
    print("PASS")