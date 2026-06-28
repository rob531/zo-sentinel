import requests
import time
import json

class MCPSubmissionsPopulatorDaemon:
    def __init__(self):
        self.write_service_url = 'http://127.0.0.1:8772/write'
        self.service_health_url = 'http://127.0.0.1:8772/service_health'
        self.mcp_server_registry_url = 'http://127.0.0.1:8772/read'
        self.interval = 60  # seconds

    def emit_heartbeat(self):
        requests.post(self.service_health_url, json={'service': 'mcp_submissions_populator_daemon', 'status': 'alive'})

    def get_new_mcp_entries(self):
        response = requests.post(self.mcp_server_registry_url, json={'table': 'mcp_server_registry', 'columns': ['id', 'name', 'ip', 'port', 'status'], 'where': {'status': 'active'}})
        return response.json()['rows']

    def get_existing_mcp_submissions(self):
        response = requests.post(self.mcp_server_registry_url, json={'table': 'mcp_submissions', 'columns': ['mcp_id']})
        return [row['mcp_id'] for row in response.json()['rows']]

    def populate_mcp_submissions(self):
        new_mcp_entries = self.get_new_mcp_entries()
        existing_mcp_submissions = self.get_existing_mcp_submissions()

        new_submissions = []
        for entry in new_mcp_entries:
            if entry['id'] not in existing_mcp_submissions:
                new_submissions.append({
                    'mcp_id': entry['id'],
                    'name': entry['name'],
                    'ip': entry['ip'],
                    'port': entry['port'],
                    'status': entry['status'],
                    'timestamp': int(time.time())
                })

        if new_submissions:
            requests.post(self.write_service_url, json={'table': 'mcp_submissions', 'rows': new_submissions, 'wait': True})

    def run(self):
        while True:
            self.emit_heartbeat()
            self.populate_mcp_submissions()
            time.sleep(self.interval)

if __name__ == '__main__':
    # Simulate new MCP entries in mcp_server_registry
    new_mcp_entries = [
        {'id': 1, 'name': 'MCP1', 'ip': '192.168.1.1', 'port': 8080, 'status': 'active'},
        {'id': 2, 'name': 'MCP2', 'ip': '192.168.1.2', 'port': 8081, 'status': 'active'}
    ]
    requests.post('http://127.0.0.1:8772/write', json={'table': 'mcp_server_registry', 'rows': new_mcp_entries, 'wait': True})

    # Run the daemon
    daemon = MCPSubmissionsPopulatorDaemon()
    daemon.run()

    # Assert that mcp_submissions is populated correctly
    response = requests.post('http://127.0.0.1:8772/read', json={'table': 'mcp_submissions', 'columns': ['mcp_id']})
    existing_mcp_submissions = [row['mcp_id'] for row in response.json()['rows']]

    assert set(existing_mcp_submissions) == {1, 2}, "mcp_submissions not populated correctly"

    print("PASS")