import time
import json
import requests
from datetime import datetime
from threading import Thread, Event

class MCPPolicyRuleEnforcerDaemon:
    def __init__(self, read_service_url, write_service_url, health_service_url):
        self.read_service_url = read_service_url
        self.write_service_url = write_service_url
        self.health_service_url = health_service_url
        self.running = Event()
        self.heartbeat_interval = 30  # seconds

    def _create_policy_violations_table(self):
        """Create the mcp_policy_violations table if it doesn't exist."""
        create_table_query = """
        CREATE TABLE IF NOT EXISTS mcp_policy_violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mcp_id TEXT NOT NULL,
            rule_id INTEGER NOT NULL,
            violation_details TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        """
        try:
            response = requests.post(
                f"{self.write_service_url}/execute",
                json={"query": create_table_query}
            )
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Error creating mcp_policy_violations table: {e}")

    def _get_active_policy_rules(self):
        """Retrieve active policy rules from mcp_policy_rules."""
        query = "SELECT id, rule_type, pattern FROM mcp_policy_rules WHERE is_active = 1"
        try:
            response = requests.post(
                f"{self.read_service_url}/execute",
                json={"query": query}
            )
            response.raise_for_status()
            return response.json().get("rows", [])
        except requests.RequestException as e:
            print(f"Error fetching policy rules: {e}")
            return []

    def _get_mcp_data(self):
        """Retrieve MCP data from mcp_server_registry."""
        query = "SELECT id, data FROM mcp_server_registry"
        try:
            response = requests.post(
                f"{self.read_service_url}/execute",
                json={"query": query}
            )
            response.raise_for_status()
            return response.json().get("rows", [])
        except requests.RequestException as e:
            print(f"Error fetching MCP data: {e}")
            return []

    def _check_violation(self, mcp_data, rule):
        """Check if MCP data violates the given rule."""
        try:
            data = json.loads(mcp_data)
            if rule["rule_type"] == "contains":
                return rule["pattern"] in json.dumps(data)
            elif rule["rule_type"] == "regex":
                import re
                return bool(re.search(rule["pattern"], json.dumps(data)))
            elif rule["rule_type"] == "equals":
                return json.dumps(data) == rule["pattern"]
            else:
                return False
        except json.JSONDecodeError:
            return False

    def _log_violation(self, mcp_id, rule_id, violation_details):
        """Log a policy violation to mcp_policy_violations."""
        timestamp = datetime.utcnow().isoformat()
        query = f"""
        INSERT INTO mcp_policy_violations (mcp_id, rule_id, violation_details, timestamp)
        VALUES ('{mcp_id}', {rule_id}, '{violation_details}', '{timestamp}')
        """
        try:
            response = requests.post(
                f"{self.write_service_url}/execute",
                json={"query": query}
            )
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Error logging violation: {e}")

    def _send_heartbeat(self):
        """Send a heartbeat to service_health."""
        while self.running.is_set():
            try:
                response = requests.post(
                    f"{self.health_service_url}/heartbeat",
                    json={"service": "mcp_policy_rule_enforcer", "status": "alive"}
                )
                response.raise_for_status()
            except requests.RequestException as e:
                print(f"Error sending heartbeat: {e}")
            time.sleep(self.heartbeat_interval)

    def run(self):
        """Start the daemon loop."""
        self.running.set()
        self._create_policy_violations_table()

        # Start heartbeat thread
        heartbeat_thread = Thread(target=self._send_heartbeat)
        heartbeat_thread.daemon = True
        heartbeat_thread.start()

        try:
            while self.running.is_set():
                rules = self._get_active_policy_rules()
                mcps = self._get_mcp_data()

                for mcp in mcps:
                    mcp_id, mcp_data = mcp["id"], mcp["data"]
                    for rule in rules:
                        if self._check_violation(mcp_data, rule):
                            violation_details = json.dumps({
                                "mcp_id": mcp_id,
                                "rule_id": rule["id"],
                                "rule_type": rule["rule_type"],
                                "pattern": rule["pattern"]
                            })
                            self._log_violation(mcp_id, rule["id"], violation_details)

                time.sleep(5)  # Polling interval
        except KeyboardInterrupt:
            self.running.clear()
            print("Daemon stopped.")

if __name__ == '__main__':
    # Mock services for testing
    class MockReadService:
        def __init__(self):
            self.rules = [
                {"id": 1, "rule_type": "contains", "pattern": "sensitive", "is_active": 1},
                {"id": 2, "rule_type": "regex", "pattern": ".*secret.*", "is_active": 1}
            ]
            self.mcps = [
                {"id": "mcp1", "data": '{"name": "test", "content": "sensitive data"}'},
                {"id": "mcp2", "data": '{"name": "test2", "content": "no secret here"}'},
                {"id": "mcp3", "data": '{"name": "test3", "content": "secret info"}'}
            ]

        def execute(self, query):
            if "mcp_policy_rules" in query:
                return {"rows": self.rules}
            elif "mcp_server_registry" in query:
                return {"rows": self.mcps}
            else:
                return {"rows": []}

    class MockWriteService:
        def __init__(self):
            self.violations = []

        def execute(self, query):
            if "mcp_policy_violations" in query and "CREATE" in query:
                return {"success": True}
            elif "INSERT INTO mcp_policy_violations" in query:
                parts = query.split("VALUES (")
                values = parts[1].split(")")[0].split(",")
                mcp_id = values[0].strip("'")
                rule_id = int(values[1])
                violation_details = values[2].strip("'")
                timestamp = values[3].strip("'")
                self.violations.append({
                    "mcp_id": mcp_id,
                    "rule_id": rule_id,
                    "violation_details": violation_details,
                    "timestamp": timestamp
                })
                return {"success": True}
            else:
                return {"success": False}

    class MockHealthService:
        def heartbeat(self, data):
            return {"status": "ok"}

    # Setup mock services
    mock_read_service = MockReadService()
    mock_write_service = MockWriteService()
    mock_health_service = MockHealthService()

    # Replace actual service URLs with mocks
    daemon = MCPPolicyRuleEnforcerDaemon(
        read_service_url="http://mock_read_service",
        write_service_url="http://mock_write_service",
        health_service_url="http://mock_health_service"
    )

    # Override the request.post method to use mock services
    import requests
    original_post = requests.post

    def mock_post(url, json=None):
        if "mock_read_service" in url:
            return original_post(url, json={"rows": mock_read_service.execute(json["query"])})
        elif "mock_write_service" in url:
            return original_post(url, json={"success": mock_write_service.execute(json["query"])})
        elif "mock_health_service" in url:
            return original_post(url, json={"status": mock_health_service.heartbeat(json)})
        else:
            return original_post(url, json=json)

    requests.post = mock_post

    # Run the daemon for a short period
    daemon.running.set()
    daemon.run()
    daemon.running.clear()

    # Check if violations were logged correctly
    expected_violations = [
        {"mcp_id": "mcp1", "rule_id": 1},
        {"mcp_id": "mcp3", "rule_id": 2}
    ]

    actual_violations = [
        {"mcp_id": v["mcp_id"], "rule_id": v["rule_id"]}
        for v in mock_write_service.violations
    ]

    if sorted(expected_violations) == sorted(actual_violations):
        print("PASS")
    else:
        print("FAIL")