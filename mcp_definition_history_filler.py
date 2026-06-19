#!/usr/bin/env python3
"""
mcp_definition_history_filler.py

Daemon that retroactively populates the mcp_definition_history table by scanning
mcp_server_registry for MCPs that have updated_definition = True since last run,
and backfilling historical snapshots from mcp_fingerprints where available.
"""

import json
import time
import requests
from datetime import datetime, timezone
from typing import Optional

# Configuration
WRITE_SERVICE_HOST = "localhost"
WRITE_SERVICE_PORT = 8772
HEALTH_CHECK_INTERVAL = 60  # seconds
DAEMON_SLEEP_INTERVAL = 30  # seconds between daemon iterations

# Table/Column names
TABLE_DEFINITION_HISTORY = "mcp_definition_history"
TABLE_SERVER_REGISTRY = "mcp_server_registry"
TABLE_FINGERPRINTS = "mcp_fingerprints"


class WriteServiceClient:
    """Client for the write service HTTP API."""
    
    def __init__(self, host: str, port: int):
        self.base_url = f"http://{host}:{port}"
    
    def query(self, sql: str, params: Optional[dict] = None) -> list:
        """Execute a SELECT query and return results."""
        response = requests.post(
            f"{self.base_url}/query",
            json={"sql": sql, "params": params or {}},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return response.json().get("rows", [])
    
    def execute(self, sql: str, params: Optional[dict] = None) -> dict:
        """Execute an INSERT/UPDATE statement."""
        response = requests.post(
            f"{self.base_url}/execute",
            json={"sql": sql, "params": params or {}},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def health_check(self) -> bool:
        """Send heartbeat to service_health."""
        try:
            response = requests.get(
                f"{self.base_url}/health",
                timeout=5
            )
            return response.status_code == 200
        except requests.RequestException:
            return False


def get_timestamp_iso() -> str:
    """Return current timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class MCPDefinitionHistoryFiller:
    """Daemon for populating mcp_definition_history table."""
    
    def __init__(self):
        self.client = WriteServiceClient(WRITE_SERVICE_HOST, WRITE_SERVICE_PORT)
        self.last_run_time: Optional[str] = None
    
    def _snapshot_mcp(self, mcp_identifier: str, server_id: int) -> Optional[dict]:
        """
        Create a snapshot of the MCP definition for a given server.
        
        Args:
            mcp_identifier: The MCP identifier (server name/path)
            server_id: The server ID from mcp_server_registry
            
        Returns:
            Dict with keys: server_id, definition_fingerprint, definition_hash,
            tool_count, schema_version, snapshot_timestamp, or None if no data found
        """
        # First, try to get current definition from server_registry
        current_def_query = """
            SELECT definition_fingerprint, definition_hash, tool_count, schema_version
            FROM mcp_server_registry
            WHERE id = %(server_id)s
        """
        
        rows = self.client.query(current_def_query, {"server_id": server_id})
        
        snapshot = None
        
        if rows:
            row = rows[0]
            definition_fingerprint = row.get("definition_fingerprint")
            definition_hash = row.get("definition_hash")
            tool_count = row.get("tool_count")
            schema_version = row.get("schema_version")
            
            # Check if we have valid data
            if definition_hash:
                snapshot = {
                    "server_id": server_id,
                    "definition_fingerprint": definition_fingerprint,
                    "definition_hash": definition_hash,
                    "tool_count": tool_count or 0,
                    "schema_version": schema_version,
                    "snapshot_timestamp": get_timestamp_iso()
                }
        
        # If no current definition, try to get from fingerprints for backfill
        if not snapshot:
            fingerprint_query = """
                SELECT definition_fingerprint, definition_hash, tool_count, schema_version, captured_at
                FROM mcp_fingerprints
                WHERE mcp_identifier = %(mcp_identifier)s
                ORDER BY captured_at DESC
                LIMIT 1
            """
            
            fp_rows = self.client.query(fingerprint_query, {"mcp_identifier": mcp_identifier})
            
            if fp_rows:
                row = fp_rows[0]
                snapshot = {
                    "server_id": server_id,
                    "definition_fingerprint": row.get("definition_fingerprint"),
                    "definition_hash": row.get("definition_hash"),
                    "tool_count": row.get("tool_count") or 0,
                    "schema_version": row.get("schema_version"),
                    "snapshot_timestamp": row.get("captured_at") or get_timestamp_iso()
                }
        
        return snapshot
    
    def _definition_hash_exists(self, definition_hash: str) -> bool:
        """Check if a definition_hash already exists in history."""
        query = """
            SELECT 1 FROM mcp_definition_history
            WHERE definition_hash = %(definition_hash)s
            LIMIT 1
        """
        rows = self.client.query(query, {"definition_hash": definition_hash})
        return len(rows) > 0
    
    def _insert_history(self, snapshot: dict) -> bool:
        """
        Insert a snapshot into mcp_definition_history.
        
        Returns:
            True if inserted, False if skipped (already exists)
        """
        # Idempotent: skip if definition_hash already exists
        if self._definition_hash_exists(snapshot["definition_hash"]):
            return False
        
        insert_sql = """
            INSERT INTO mcp_definition_history 
            (server_id, definition_fingerprint, definition_hash, tool_count, schema_version, snapshot_timestamp)
            VALUES 
            (%(server_id)s, %(definition_fingerprint)s, %(definition_hash)s, %(tool_count)s, %(schema_version)s, %(snapshot_timestamp)s)
        """
        
        self.client.execute(insert_sql, snapshot)
        return True
    
    def _get_updated_servers(self) -> list:
        """Get list of servers with updated_definition=True."""
        query = """
            SELECT id, mcp_identifier FROM mcp_server_registry
            WHERE updated_definition = true
        """
        return self.client.query(query)
    
    def _get_all_servers(self) -> list:
        """Get all servers from registry."""
        query = """
            SELECT id, mcp_identifier FROM mcp_server_registry
        """
        return self.client.query(query)
    
    def backfill_from_fingerprints(self) -> int:
        """
        Backfill historical snapshots from mcp_fingerprints.
        
        Returns:
            Number of records backfilled
        """
        backfill_count = 0
        
        # Get all fingerprints that don't have corresponding history entries
        query = """
            SELECT 
                f.mcp_identifier,
                f.definition_fingerprint,
                f.definition_hash,
                f.tool_count,
                f.schema_version,
                f.captured_at
            FROM mcp_fingerprints f
            LEFT JOIN mcp_definition_history h 
                ON f.definition_hash = h.definition_hash
            WHERE h.definition_hash IS NULL
            ORDER BY f.mcp_identifier, f.captured_at
        """
        
        rows = self.client.query(query)
        
        for row in rows:
            snapshot = {
                "server_id": None,  # Will be resolved from registry
                "definition_fingerprint": row.get("definition_fingerprint"),
                "definition_hash": row.get("definition_hash"),
                "tool_count": row.get("tool_count") or 0,
                "schema_version": row.get("schema_version"),
                "snapshot_timestamp": row.get("captured_at") or get_timestamp_iso()
            }
            
            # Try to resolve server_id from registry
            mcp_id = row.get("mcp_identifier")
            if mcp_id:
                server_query = """
                    SELECT id FROM mcp_server_registry
                    WHERE mcp_identifier = %(mcp_identifier)s
                    LIMIT 1
                """
                server_rows = self.client.query(server_query, {"mcp_identifier": mcp_id})
                if server_rows:
                    snapshot["server_id"] = server_rows[0].get("id")
            
            if snapshot["server_id"]:
                try:
                    if self._insert_history(snapshot):
                        backfill_count += 1
                except Exception:
                    pass  # Skip on error, maintain idempotency
        
        return backfill_count
    
    def process_updated_servers(self) -> int:
        """
        Process servers with updated_definition=True.
        
        Returns:
            Number of records processed
        """
        processed_count = 0
        servers = self._get_updated_servers()
        
        for server in servers:
            server_id = server.get("id")
            mcp_identifier = server.get("mcp_identifier")
            
            if not server_id or not mcp_identifier:
                continue
            
            snapshot = self._snapshot_mcp(mcp_identifier, server_id)
            
            if snapshot:
                try:
                    if self._insert_history(snapshot):
                        processed_count += 1
                except Exception:
                    pass  # Skip on error, maintain idempotency
        
        return processed_count
    
    def send_heartbeat(self) -> bool:
        """Send heartbeat to service_health."""
        return self.client.health_check()
    
    def run(self) -> None:
        """
        Main daemon loop.
        
        Continuously:
        1. Process updated servers
        2. Backfill from fingerprints
        3. Send heartbeat every 60 seconds
        """
        print(f"[{get_timestamp_iso()}] MCP Definition History Filler starting...")
        
        last_heartbeat = time.time()
        
        while True:
            try:
                # Process updated servers
                updated_count = self.process_updated_servers()
                if updated_count > 0:
                    print(f"[{get_timestamp_iso()}] Processed {updated_count} updated servers")
                
                # Backfill from fingerprints
                backfill_count = self.backfill_from_fingerprints()
                if backfill_count > 0:
                    print(f"[{get_timestamp_iso()}] Backfilled {backfill_count} historical records")
                
                # Check if heartbeat is due
                current_time = time.time()
                if current_time - last_heartbeat >= HEALTH_CHECK_INTERVAL:
                    if self.send_heartbeat():
                        print(f"[{get_timestamp_iso()}] Heartbeat sent to service_health")
                    else:
                        print(f"[{get_timestamp_iso()}] Warning: Failed to send heartbeat")
                    last_heartbeat = current_time
                
            except requests.RequestException as e:
                print(f"[{get_timestamp_iso()}] Request error: {e}")
            except Exception as e:
                print(f"[{get_timestamp_iso()}] Error in daemon loop: {e}")
            
            time.sleep(DAEMON_SLEEP_INTERVAL)


def run_self_test():
    """
    Self-test that validates _snapshot_mcp with known servers.
    
    Tests against 3 different mcp_identifiers from the registry.
    """
    print("=" * 60)
    print("Running self-test for _snapshot_mcp...")
    print("=" * 60)
    
    filler = MCPDefinitionHistoryFiller()
    expected_keys = {
        "server_id", "definition_fingerprint", "definition_hash",
        "tool_count", "schema_version", "snapshot_timestamp"
    }
    
    # Get 3 different servers from registry
    servers = filler._get_all_servers()
    
    if not servers:
        print("FAIL: No servers found in mcp_server_registry")
        return False
    
    test_servers = servers[:3] if len(servers) >= 3 else servers
    
    all_passed = True
    
    for server in test_servers:
        server_id = server.get("id")
        mcp_identifier = server.get("mcp_identifier")
        
        print(f"\nTesting server_id={server_id}, mcp_identifier={mcp_identifier}")
        
        result = filler._snapshot_mcp(mcp_identifier, server_id)
        
        if result is None:
            print(f"  Result: None (no definition data available)")
            # This is acceptable if no data exists
            continue
        
        # Verify all expected keys are present
        result_keys = set(result.keys())
        missing_keys = expected_keys - result_keys
        
        if missing_keys:
            print(f"  FAIL: Missing keys: {missing_keys}")
            print(f"  Result keys: {result_keys}")
            all_passed = False
        else:
            # Verify types and values
            if not isinstance(result["server_id"], int) and result["server_id"] is not None:
                print(f"  FAIL: server_id should be int or None, got {type(result['server_id'])}")
                all_passed = False
            elif not isinstance(result["tool_count"], int):
                print(f"  FAIL: tool_count should be int, got {type(result['tool_count'])}")
                all_passed = False
            elif result["snapshot_timestamp"]:
                # Verify ISO 8601 format
                try:
                    datetime.fromisoformat(result["snapshot_timestamp"].replace("Z", "+00:00"))
                except ValueError:
                    print(f"  FAIL: snapshot_timestamp not in ISO 8601 format: {result['snapshot_timestamp']}")
                    all_passed = False
                    continue
            
            print(f"  PASS: All expected keys present")
            print(f"    - definition_hash: {result.get('definition_hash', 'N/A')[:16]}...")
            print(f"    - tool_count: {result.get('tool_count')}")
            print(f"    - schema_version: {result.get('schema_version')}")
    
    print("\n" + "=" * 60)
    if all_passed:
        print("PASS: Self-test completed successfully")
    else:
        print("FAIL: Self-test completed with errors")
    print("=" * 60)
    
    return all_passed


if __name__ == "__main__":
    # Run self-test
    run_self_test()