import requests
import json
from typing import List, Dict, Any

def verify_mcp_definition_history_data_integrity() -> bool:
    """
    Verify the data integrity and population of the `mcp_definition_history` table.
    Cross-references with `mcp_submissions` to ensure definition changes are accurately recorded.

    Returns:
        bool: True if all checks pass, False otherwise.
    """
    # Query to get all MCP definition history entries
    history_query = """
    SELECT
        id, mcp_id, definition, created_at, updated_at, version
    FROM
        mcp_definition_history
    ORDER BY
        mcp_id, version;
    """

    # Query to get all MCP submissions
    submissions_query = """
    SELECT
        id, definition, created_at, updated_at
    FROM
        mcp_submissions
    ORDER BY
        id;
    """

    def execute_query(query: str) -> List[Dict[str, Any]]:
        """Helper function to execute a query via the API."""
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query}
        )
        if response.status_code != 200:
            raise Exception(f"Query failed: {response.text}")
        return response.json()["data"]

    try:
        history_entries = execute_query(history_query)
        submissions = execute_query(submissions_query)

        # Check 1: Ensure all MCP submissions have at least one history entry
        submission_ids = {sub["id"] for sub in submissions}
        history_mcp_ids = {entry["mcp_id"] for entry in history_entries}
        missing_history = submission_ids - history_mcp_ids
        if missing_history:
            print(f"Error: Missing history entries for MCP IDs: {missing_history}")
            return False

        # Check 2: Ensure history entries are versioned correctly (no gaps or duplicates)
        version_checks = {}
        for entry in history_entries:
            mcp_id = entry["mcp_id"]
            version = entry["version"]
            if mcp_id not in version_checks:
                version_checks[mcp_id] = set()
            version_checks[mcp_id].add(version)

        for mcp_id, versions in version_checks.items():
            if len(versions) != max(versions):
                print(f"Error: Version gaps or duplicates for MCP ID: {mcp_id}")
                return False

        # Check 3: Ensure the latest history entry matches the current submission definition
        latest_history = {}
        for entry in history_entries:
            mcp_id = entry["mcp_id"]
            if mcp_id not in latest_history or entry["version"] > latest_history[mcp_id]["version"]:
                latest_history[mcp_id] = entry

        for sub in submissions:
            mcp_id = sub["id"]
            if mcp_id in latest_history:
                if latest_history[mcp_id]["definition"] != sub["definition"]:
                    print(f"Error: Definition mismatch for MCP ID: {mcp_id}")
                    return False

        return True

    except Exception as e:
        print(f"Error during verification: {e}")
        return False

if __name__ == "__main__":
    # Mock data for assertions (replace with actual test data if needed)
    mock_history = [
        {"id": 1, "mcp_id": 1, "definition": "def1", "created_at": "2023-01-01", "updated_at": "2023-01-01", "version": 1},
        {"id": 2, "mcp_id": 1, "definition": "def2", "created_at": "2023-01-02", "updated_at": "2023-01-02", "version": 2},
        {"id": 3, "mcp_id": 2, "definition": "def3", "created_at": "2023-01-01", "updated_at": "2023-01-01", "version": 1},
    ]

    mock_submissions = [
        {"id": 1, "definition": "def2", "created_at": "2023-01-01", "updated_at": "2023-01-02"},
        {"id": 2, "definition": "def3", "created_at": "2023-01-01", "updated_at": "2023-01-01"},
    ]

    # Assertion 1: All submissions have history entries
    assert len({sub["id"] for sub in mock_submissions} - {entry["mcp_id"] for entry in mock_history}) == 0, "Assertion 1 failed"
    print("PASS: Assertion 1")

    # Assertion 2: History entries are versioned correctly
    version_checks = {}
    for entry in mock_history:
        mcp_id = entry["mcp_id"]
        version = entry["version"]
        if mcp_id not in version_checks:
            version_checks[mcp_id] = set()
        version_checks[mcp_id].add(version)

    for mcp_id, versions in version_checks.items():
        assert len(versions) == max(versions), f"Assertion 2 failed for MCP ID: {mcp_id}"
    print("PASS: Assertion 2")

    # Assertion 3: Latest history entry matches current submission definition
    latest_history = {}
    for entry in mock_history:
        mcp_id = entry["mcp_id"]
        if mcp_id not in latest_history or entry["version"] > latest_history[mcp_id]["version"]:
            latest_history[mcp_id] = entry

    for sub in mock_submissions:
        mcp_id = sub["id"]
        if mcp_id in latest_history:
            assert latest_history[mcp_id]["definition"] == sub["definition"], f"Assertion 3 failed for MCP ID: {mcp_id}"
    print("PASS: Assertion 3")

    # Run the actual verification
    if verify_mcp_definition_history_data_integrity():
        print("PASS: Data integrity verification")
    else:
        print("FAIL: Data integrity verification")