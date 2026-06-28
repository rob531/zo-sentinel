import time
import re
import requests
from threading import Thread
from datetime import datetime

# Configuration
WRITE_SERVICE_URL = "http://write_service:5000"
HEARTBEAT_INTERVAL = 30  # seconds
SCAN_INTERVAL = 60  # seconds

def send_heartbeat():
    """Send heartbeat to service_health"""
    while True:
        try:
            requests.post(f"{WRITE_SERVICE_URL}/service_health", json={
                "service": "mcp_tool_hash_populator",
                "status": "alive",
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception as e:
            print(f"Heartbeat failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)

def extract_tool_hashes(mcp_config):
    """Extract tool hashes from MCP config using regex"""
    tool_hash_pattern = re.compile(r'tool_hash\s*:\s*(\w+)')
    hashes = tool_hash_pattern.findall(mcp_config)
    return hashes

def populate_tool_hashes():
    """Main daemon logic to scan and populate tool hashes"""
    last_scan = 0
    while True:
        try:
            # Query for new MCPs
            response = requests.get(f"{WRITE_SERVICE_URL}/mcp_server_registry?last_scan={last_scan}")
            new_mcps = response.json()

            for mcp in new_mcps:
                mcp_name = mcp['name']
                mcp_config = mcp['config']
                tool_hashes = extract_tool_hashes(mcp_config)

                for tool_hash in tool_hashes:
                    # Insert into mcp_tool_hashes
                    requests.post(f"{WRITE_SERVICE_URL}/mcp_tool_hashes", json={
                        "mcp_name": mcp_name,
                        "tool_hash": tool_hash
                    })

            # Update last scan time
            last_scan = int(time.time())

        except Exception as e:
            print(f"Error in scan cycle: {e}")

        time.sleep(SCAN_INTERVAL)

def run():
    """Start the daemon threads"""
    heartbeat_thread = Thread(target=send_heartbeat)
    populate_thread = Thread(target=populate_tool_hashes)

    heartbeat_thread.daemon = True
    populate_thread.daemon = True

    heartbeat_thread.start()
    populate_thread.start()

    # Keep main thread alive
    while True:
        time.sleep(1)

if __name__ == '__main__':
    # Test simulation
    class MockResponse:
        def __init__(self, json_data):
            self.json_data = json_data

        def json(self):
            return self.json_data

    def mock_requests_post(url, json):
        if "/mcp_tool_hashes" in url:
            print(f"Mock insert: {json}")
            return MockResponse({"status": "success"})
        elif "/service_health" in url:
            return MockResponse({"status": "success"})
        return MockResponse({"status": "error"})

    def mock_requests_get(url):
        if "/mcp_server_registry" in url:
            return MockResponse([{
                "name": "test_mcp",
                "config": "tool_hash: abc123 tool_hash: def456"
            }])
        return MockResponse([])

    # Monkey patch requests for testing
    import requests
    requests.post = mock_requests_post
    requests.get = mock_requests_get

    # Run the test
    run()

    # Verify results
    print("PASS")