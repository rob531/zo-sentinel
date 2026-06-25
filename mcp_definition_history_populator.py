import time
import requests
from threading import Thread
from datetime import datetime

class MCPDefinitionHistoryPopulator:
    def __init__(self):
        self.last_heartbeat = time.time()
        self.heartbeat_interval = 60
        self.write_service_url = "http://127.0.0.1:8772/write"
        self.service_health_url = "http://127.0.0.1:8772/health"
        self.last_checked = datetime.min

    def heartbeat(self):
        while True:
            if time.time() - self.last_heartbeat >= self.heartbeat_interval:
                requests.post(self.service_health_url, json={"status": "alive"})
                self.last_heartbeat = time.time()
            time.sleep(10)

    def get_mcp_definitions(self):
        response = requests.get("http://127.0.0.1:8772/mcp_server_registry")
        return response.json() if response.status_code == 200 else []

    def write_to_history(self, definition):
        data = {
            "timestamp": datetime.utcnow().isoformat(),
            "definition": definition
        }
        requests.post(self.write_service_url, json=data)

    def run(self):
        heartbeat_thread = Thread(target=self.heartbeat)
        heartbeat_thread.daemon = True
        heartbeat_thread.start()

        while True:
            definitions = self.get_mcp_definitions()
            for definition in definitions:
                if definition["last_updated"] > self.last_checked:
                    self.write_to_history(definition)
            self.last_checked = datetime.utcnow()
            time.sleep(5)

if __name__ == "__main__":
    # Mock setup for testing
    class MockRequests:
        def __init__(self):
            self.history = []
            self.registry = [
                {"id": 1, "definition": "def1", "last_updated": datetime(2023, 1, 1)},
                {"id": 2, "definition": "def2", "last_updated": datetime(2023, 1, 2)}
            ]

        def get(self, url):
            if url.endswith("mcp_server_registry"):
                return type('Response', (), {
                    'json': lambda: self.registry,
                    'status_code': 200
                })()
            return type('Response', (), {'status_code': 404})()

        def post(self, url, json):
            if url.endswith("write"):
                self.history.append(json)
            return type('Response', (), {'status_code': 200})()

    requests = MockRequests()
    populator = MCPDefinitionHistoryPopulator()

    # Simulate new definition
    requests.registry.append({"id": 3, "definition": "def3", "last_updated": datetime(2023, 1, 3)})

    # Run once for test
    populator.run_once = lambda: populator.run()
    populator.run_once()

    # Check if new definition was written to history
    assert len(requests.history) == 1
    assert requests.history[0]["definition"]["id"] == 3
    print("PASS")