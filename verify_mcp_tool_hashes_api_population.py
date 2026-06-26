import requests
from write_service import WriteService

class MCPToolHashesVerifier:
    def __init__(self, api_base_url, write_service):
        self.api_base_url = api_base_url
        self.write_service = write_service

    def simulate_api_calls(self, tool_hashes):
        for tool_hash in tool_hashes:
            response = requests.post(
                f"{self.api_base_url}/mcp_tool_hashes",
                json=tool_hash
            )
            if response.status_code != 201:
                raise Exception(f"API call failed for {tool_hash}: {response.text}")

    def verify_table_population(self):
        query = "SELECT COUNT(*) FROM mcp_tool_hashes"
        result = self.write_service.execute_query(query)
        count = result.fetchone()[0]
        if count < 3:
            raise Exception(f"Expected at least 3 entries, found {count}")

        query = "SELECT * FROM mcp_tool_hashes"
        result = self.write_service.execute_query(query)
        rows = result.fetchall()
        for row in rows:
            if not all(row):
                raise Exception(f"Invalid row structure: {row}")

    def run_verification(self, tool_hashes):
        self.simulate_api_calls(tool_hashes)
        self.verify_table_population()
        print("PASS")

if __name__ == "__main__":
    # Initialize WriteService (assuming it's properly configured elsewhere)
    write_service = WriteService()

    # Simulate API base URL
    api_base_url = "http://localhost:5000"

    # Sample tool hashes to simulate API calls
    tool_hashes = [
        {"tool_name": "tool1", "hash": "hash1", "algorithm": "sha256"},
        {"tool_name": "tool2", "hash": "hash2", "algorithm": "sha256"},
        {"tool_name": "tool3", "hash": "hash3", "algorithm": "sha256"}
    ]

    verifier = MCPToolHashesVerifier(api_base_url, write_service)
    verifier.run_verification(tool_hashes)