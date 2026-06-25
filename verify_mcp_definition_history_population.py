import unittest
from datetime import datetime, timedelta
from typing import Dict, Any

class MockWriteService:
    def __init__(self):
        self.mcp_server_registry = []
        self.mcp_definition_history = []

    def query(self, table_name: str, query: str) -> list:
        if table_name == "mcp_server_registry":
            return self.mcp_server_registry
        elif table_name == "mcp_definition_history":
            return self.mcp_definition_history
        else:
            raise ValueError(f"Unknown table: {table_name}")

def run_verification(write_service) -> tuple[bool, Dict[str, Any]]:
    """
    Verify the successful population of the `mcp_definition_history` table.

    Args:
        write_service: The write service to use for DB queries.

    Returns:
        A tuple containing a boolean indicating success or failure, and a detailed report dictionary.
    """
    report = {
        "status": "success",
        "details": {},
        "errors": []
    }

    try:
        # Get recent entries from mcp_definition_history
        history_entries = write_service.query(
            "mcp_definition_history",
            "SELECT * FROM mcp_definition_history ORDER BY created_at DESC LIMIT 10"
        )

        if not history_entries:
            report["status"] = "failure"
            report["errors"].append("No entries found in mcp_definition_history table.")
            return False, report

        # Get all entries from mcp_server_registry
        registry_entries = write_service.query(
            "mcp_server_registry",
            "SELECT * FROM mcp_server_registry"
        )

        if not registry_entries:
            report["status"] = "failure"
            report["errors"].append("No entries found in mcp_server_registry table.")
            return False, report

        # Verify that history entries align with registry entries
        registry_ids = {entry["id"] for entry in registry_entries}
        history_ids = {entry["mcp_server_id"] for entry in history_entries}

        missing_ids = registry_ids - history_ids
        if missing_ids:
            report["status"] = "failure"
            report["errors"].append(f"Missing history entries for MCP server IDs: {missing_ids}")
            return False, report

        # Verify that history entries have recent timestamps
        now = datetime.now()
        recent_threshold = now - timedelta(hours=24)
        recent_entries = [
            entry for entry in history_entries
            if datetime.fromisoformat(entry["created_at"]) >= recent_threshold
        ]

        if not recent_entries:
            report["status"] = "failure"
            report["errors"].append("No recent entries found in mcp_definition_history table.")
            return False, report

        report["details"]["recent_entries_count"] = len(recent_entries)
        report["details"]["total_history_entries"] = len(history_entries)
        report["details"]["total_registry_entries"] = len(registry_entries)

        return True, report

    except Exception as e:
        report["status"] = "failure"
        report["errors"].append(f"An error occurred during verification: {str(e)}")
        return False, report

if __name__ == "__main__":
    # Seed mock data
    write_service = MockWriteService()

    # Seed mcp_server_registry
    write_service.mcp_server_registry = [
        {"id": 1, "name": "server1", "definition": "def1"},
        {"id": 2, "name": "server2", "definition": "def2"},
        {"id": 3, "name": "server3", "definition": "def3"},
    ]

    # Seed mcp_definition_history
    now = datetime.now()
    write_service.mcp_definition_history = [
        {"id": 1, "mcp_server_id": 1, "definition": "def1", "created_at": (now - timedelta(hours=1)).isoformat()},
        {"id": 2, "mcp_server_id": 2, "definition": "def2", "created_at": (now - timedelta(hours=2)).isoformat()},
        {"id": 3, "mcp_server_id": 3, "definition": "def3", "created_at": (now - timedelta(hours=3)).isoformat()},
        {"id": 4, "mcp_server_id": 1, "definition": "def1_updated", "created_at": now.isoformat()},
    ]

    # Run verification
    success, report = run_verification(write_service)

    if success:
        print("PASS")
    else:
        print("FAIL")
        for error in report["errors"]:
            print(f"  - {error}")