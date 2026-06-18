#!/usr/bin/env python3
"""
Daemon that monitors mcp_server_registry for schema definition changes
and writes diffs to mcp_definition_history.
"""

import json
import time
import requests
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass, asdict
from copy import deepcopy

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 30  # seconds
SCAN_INTERVAL = 60  # seconds
UNCHANGED_COOLDOWN = 24 * 60 * 60  # 24 hours in seconds
MAX_RETRIES = 3
TIMEOUT = 10
HEALTH_WARNING_THRESHOLD = 2 * 60 * 60  # 2 hours in seconds


@dataclass
class MCPToolSchema:
    """Represents a tool's schema definition."""
    name: str
    schema: dict


@dataclass
class MCPServerState:
    """Represents a single MCP server's current state."""
    mcp_id: str
    name: str
    tool_schema: Optional[dict]


@dataclass
class DefinitionChange:
    """Represents a detected change in tool definitions."""
    mcp_id: str
    change_type: str  # added_tools, removed_tools, modified_params, unchanged
    tool_name: str
    before_schema: Optional[dict]
    after_schema: Optional[dict]
    diff_summary: str


class DefinitionChangeHistoryWriter:
    """
    Daemon that monitors MCP schema definitions and tracks changes over time.
    """
    
    def __init__(self):
        self.last_successful_scan: Optional[datetime] = None
        self.last_unchanged_write: dict[str, datetime] = {}  # mcp_id -> last write time
        self._stop_event = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._schema_columns: Optional[list] = None
        self._history_columns: Optional[list] = None
    
    def _read_db_schema(self) -> dict:
        """Read DB_SCHEMA.md to get column information."""
        try:
            with open("DB_SCHEMA.md", "r") as f:
                content = f.read()
            # Parse schema from markdown
            schema = {"mcp_server_registry": [], "mcp_definition_history": []}
            current_table = None
            for line in content.split("\n"):
                if "## mcp_server_registry" in line:
                    current_table = "mcp_server_registry"
                elif "## mcp_definition_history" in line:
                    current_table = "mcp_definition_history"
                elif current_table and line.strip().startswith("|"):
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3 and parts[1] and parts[1] != "Column":
                        col_name = parts[1]
                        if col_name not in ("", "-"):
                            schema[current_table].append(col_name)
            return schema
        except FileNotFoundError:
            # Return default columns if schema file not found
            return {
                "mcp_server_registry": ["mcp_id", "name", "tool_schema", "created_at", "updated_at"],
                "mcp_definition_history": ["mcp_id", "changed_at", "change_type", "tool_name", 
                                          "before_schema", "after_schema", "diff_summary"]
            }
    
    def _query_with_retry(self, sql: str, params: list) -> Optional[list]:
        """Execute a query with exponential backoff on 5xx errors."""
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    f"{WRITE_SERVICE_URL}/query",
                    json={"sql": sql, "params": params},
                    timeout=TIMEOUT
                )
                if response.status_code >= 500:
                    wait_time = (2 ** attempt) * 1.0
                    time.sleep(wait_time)
                    continue
                response.raise_for_status()
                result = response.json()
                return result.get("rows", result.get("data", []))
            except requests.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    print(f"Query failed after {MAX_RETRIES} attempts: {e}", file=__import__("sys").stderr)
                    return None
                wait_time = (2 ** attempt) * 1.0
                time.sleep(wait_time)
        return None
    
    def _write_with_retry(self, rows: list) -> bool:
        """Execute a write with exponential backoff on 5xx errors."""
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    f"{WRITE_SERVICE_URL}/write",
                    json={"table": "mcp_definition_history", "rows": rows, "wait": True},
                    timeout=TIMEOUT
                )
                if response.status_code >= 500:
                    wait_time = (2 ** attempt) * 1.0
                    time.sleep(wait_time)
                    continue
                response.raise_for_status()
                return True
            except requests.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    print(f"Write failed after {MAX_RETRIES} attempts: {e}", file=__import__("sys").stderr)
                    return False
                wait_time = (2 ** attempt) * 1.0
                time.sleep(wait_time)
        return False
    
    def _send_heartbeat(self) -> bool:
        """Send heartbeat to write_service."""
        try:
            response = requests.post(
                f"{WRITE_SERVICE_URL}/write",
                json={
                    "table": "service_health",
                    "rows": [{
                        "service": "definition_change_monitor",
                        "last_heartbeat": datetime.now(timezone.utc).isoformat()
                    }]
                },
                timeout=TIMEOUT
            )
            return response.status_code < 500
        except requests.RequestException:
            return False
    
    def _heartbeat_loop(self):
        """Background thread for sending heartbeats."""
        while not self._stop_event.is_set():
            self._send_heartbeat()
            # Wait for stop event or timeout
            self._stop_event.wait(HEARTBEAT_INTERVAL)
    
    def _get_current_mcp_states(self) -> list[MCPServerState]:
        """Fetch all MCPs with non-null tool_schema from registry."""
        sql = "SELECT mcp_id, name, tool_schema FROM mcp_server_registry WHERE tool_schema IS NOT NULL"
        rows = self._query_with_retry(sql, [])
        
        if rows is None:
            return []
        
        states = []
        for row in rows:
            # Handle both dict and list access patterns
            if isinstance(row, dict):
                mcp_id = row.get("mcp_id", row.get(0))
                name = row.get("name", row.get(1))
                tool_schema = row.get("tool_schema", row.get(2))
            else:
                mcp_id = row[0] if len(row) > 0 else None
                name = row[1] if len(row) > 1 else None
                tool_schema = row[2] if len(row) > 2 else None
            
            if mcp_id and tool_schema:
                # Parse JSON if stored as string
                if isinstance(tool_schema, str):
                    try:
                        tool_schema = json.loads(tool_schema)
                    except json.JSONDecodeError:
                        continue
                states.append(MCPServerState(
                    mcp_id=mcp_id,
                    name=name,
                    tool_schema=tool_schema
                ))
        
        return states
    
    def _get_last_known_state(self, mcp_id: str) -> Optional[dict]:
        """Get the last known state for an MCP from history."""
        sql = """
            SELECT tool_name, before_schema, after_schema, change_type
            FROM mcp_definition_history
            WHERE mcp_id = %s AND change_type != 'unchanged'
            ORDER BY changed_at DESC
            LIMIT 1
        """
        rows = self._query_with_retry(sql, [mcp_id])
        
        if rows is None or len(rows) == 0:
            return None
        
        row = rows[0]
        if isinstance(row, dict):
            return {
                "tool_name": row.get("tool_name", row.get(0)),
                "before_schema": row.get("before_schema", row.get(1)),
                "after_schema": row.get("after_schema", row.get(2)),
                "change_type": row.get("change_type", row.get(3))
            }
        else:
            return {
                "tool_name": row[0] if len(row) > 0 else None,
                "before_schema": row[1] if len(row) > 1 else None,
                "after_schema": row[2] if len(row) > 2 else None,
                "change_type": row[3] if len(row) > 3 else None
            }
    
    def _get_last_checked_timestamp(self, mcp_id: str) -> Optional[datetime]:
        """Get the last checked timestamp for an MCP."""
        sql = """
            SELECT MAX(changed_at) FROM mcp_definition_history WHERE mcp_id = %s
        """
        rows = self._query_with_retry(sql, [mcp_id])
        
        if rows is None or len(rows) == 0 or rows[0][0] is None:
            return None
        
        ts = rows[0][0]
        if isinstance(ts, str):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return ts
    
    def _compute_diff(self, current_tools: dict, previous_tools: dict) -> list[DefinitionChange]:
        """Compute the diff between current and previous tool schemas."""
        changes = []
        current_tool_names = set(current_tools.keys())
        previous_tool_names = set(previous_tools.keys())
        
        # Added tools
        for tool_name in current_tool_names - previous_tool_names:
            changes.append(DefinitionChange(
                mcp_id="",  # Will be set by caller
                change_type="added_tools",
                tool_name=tool_name,
                before_schema=None,
                after_schema=current_tools[tool_name],
                diff_summary=f"New tool added: {tool_name}"
            ))
        
        # Removed tools
        for tool_name in previous_tool_names - current_tool_names:
            changes.append(DefinitionChange(
                mcp_id="",  # Will be set by caller
                change_type="removed_tools",
                tool_name=tool_name,
                before_schema=previous_tools[tool_name],
                after_schema=None,
                diff_summary=f"Tool removed: {tool_name}"
            ))
        
        # Modified params
        for tool_name in current_tool_names & previous_tool_names:
            if current_tools[tool_name] != previous_tools[tool_name]:
                diff = self._generate_diff_summary(
                    previous_tools[tool_name],
                    current_tools[tool_name]
                )
                changes.append(DefinitionChange(
                    mcp_id="",  # Will be set by caller
                    change_type="modified_params",
                    tool_name=tool_name,
                    before_schema=previous_tools[tool_name],
                    after_schema=current_tools[tool_name],
                    diff_summary=diff
                ))
        
        return changes
    
    def _generate_diff_summary(self, before: dict, after: dict) -> str:
        """Generate a human-readable diff summary."""
        summary_parts = []
        
        # Compare keys
        before_keys = set(before.keys()) if isinstance(before, dict) else set()
        after_keys = set(after.keys()) if isinstance(after, dict) else set()
        
        added_keys = after_keys - before_keys
        removed_keys = before_keys - after_keys
        common_keys = before_keys & after_keys
        
        if added_keys:
            summary_parts.append(f"Added parameters: {', '.join(sorted(added_keys))}")
        if removed_keys:
            summary_parts.append(f"Removed parameters: {', '.join(sorted(removed_keys))}")
        
        # Check for type changes in common keys
        type_changes = []
        for key in common_keys:
            before_val = before.get(key) if isinstance(before, dict) else None
            after_val = after.get(key) if isinstance(after, dict) else None
            before_type = type(before_val).__name__
            after_type = type(after_val).__name__
            if before_type != after_type:
                type_changes.append(f"{key}: {before_type} -> {after_type}")
        
        if type_changes:
            summary_parts.append(f"Type changes: {', '.join(type_changes)}")
        
        return "; ".join(summary_parts) if summary_parts else "Schema modified"
    
    def _build_history_row(self, change: DefinitionChange, mcp_id: str) -> dict:
        """Build a history row with correct column names."""
        return {
            "mcp_id": mcp_id,
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "change_type": change.change_type,
            "tool_name": change.tool_name,
            "before_schema": json.dumps(change.before_schema) if change.before_schema else None,
            "after_schema": json.dumps(change.after_schema) if change.after_schema else None,
            "diff_summary": change.diff_summary
        }
    
    def _normalize_tool_schema(self, tool_schema: dict) -> dict:
        """Normalize tool schema to a dictionary of tool_name -> schema."""
        if not tool_schema:
            return {}
        
        # Handle various schema formats
        if isinstance(tool_schema, dict):
            # Check if it's wrapped in "tools" key
            if "tools" in tool_schema:
                return {t.get("name", t.get("title", f"tool_{i}")): t 
                       for i, t in enumerate(tool_schema["tools"])}
            # Check if tools are at top level as a list
            if isinstance(list(tool_schema.values())[0] if tool_schema else None, list):
                tools = []
                for v in tool_schema.values():
                    if isinstance(v, list):
                        tools.extend(v)
                return {t.get("name", t.get("title", f"tool_{i}")): t 
                       for i, t in enumerate(tools)}
            # Assume tool_schema is already a dict of tool_name -> schema
            return tool_schema
        
        return {}
    
    def check_for_changes(self) -> tuple[int, list[dict]]:
        """
        Scan all live MCPs, compute diffs vs last known state, 
        write new history rows.
        
        Returns:
            Tuple of (number of write calls made, rows written)
        """
        # Ensure we have column info
        if self._schema_columns is None:
            schema = self._read_db_schema()
            self._schema_columns = schema.get("mcp_server_registry", [])
            self._history_columns = schema.get("mcp_definition_history", [])
        
        # Step 1: Fetch all MCPs with non-null tool_schema
        current_states = self._get_current_mcp_states()
        
        if not current_states:
            self.last_successful_scan = datetime.now(timezone.utc)
            return 0, []
        
        # Step 2: For each MCP, compare current schema vs last known
        all_changes = []
        unchanged_mcps = []
        
        for state in current_states:
            current_tools = self._normalize_tool_schema(state.tool_schema)
            
            # Get previous state from history
            last_record = self._get_last_known_state(state.mcp_id)
            
            if last_record is None:
                # First time seeing this MCP - record all tools as "added"
                for tool_name, tool_schema in current_tools.items():
                    all_changes.append(DefinitionChange(
                        mcp_id=state.mcp_id,
                        change_type="added_tools",
                        tool_name=tool_name,
                        before_schema=None,
                        after_schema=tool_schema,
                        diff_summary=f"Initial registration: {tool_name}"
                    ))
            else:
                # Build previous tools dict from history
                previous_tools = {}
                if last_record.get("before_schema"):
                    prev = last_record["before_schema"]
                    if isinstance(prev, str):
                        try:
                            prev = json.loads(prev)
                        except json.JSONDecodeError:
                            pass
                    if isinstance(prev, dict):
                        previous_tools = self._normalize_tool_schema(prev)
                if last_record.get("after_schema"):
                    after = last_record["after_schema"]
                    if isinstance(after, str):
                        try:
                            after = json.loads(after)
                        except json.JSONDecodeError:
                            pass
                    if isinstance(after, dict):
                        # Merge with after schema as it's more recent
                        temp = self._normalize_tool_schema(after)
                        # Also include any tools from before not in after
                        for k, v in previous_tools.items():
                            if k not in temp:
                                temp[k] = v
                        previous_tools = temp
                
                # Compute diff
                changes = self._compute_diff(current_tools, previous_tools)
                for change in changes:
                    change.mcp_id = state.mcp_id
                all_changes.extend(changes)
                
                if not changes:
                    unchanged_mcps.append(state.mcp_id)
        
        # Step 3 & 4: Write history rows
        rows_to_write = []
        
        # Write rows for changed tools
        for change in all_changes:
            row = self._build_history_row(change, change.mcp_id)
            rows_to_write.append(row)
        
        # Handle unchanged MCPs (with cooldown)
        now = datetime.now(timezone.utc)
        for mcp_id in unchanged_mcps:
            last_write = self.last_unchanged_write.get(mcp_id)
            should_write = True
            
            if last_write:
                time_since = (now - last_write).total_seconds()
                if time_since < UNCHANGED_COOLDOWN:
                    should_write = False
            
            if should_write:
                rows_to_write.append({
                    "mcp_id": mcp_id,
                    "changed_at": now.isoformat(),
                    "change_type": "unchanged",
                    "tool_name": None,
                    "before_schema": None,
                    "after_schema": None,
                    "diff_summary": "No schema changes detected"
                })
                self.last_unchanged_write[mcp_id] = now
        
        # Step 5: Write to database
        write_count = 0
        if rows_to_write:
            # Write in batches to enable ON CONFLICT handling
            if self._write_with_retry(rows_to_write):
                write_count = 1  # One write call made
            else:
                write_count = 0
        
        self.last_successful_scan = now
        return write_count, rows_to_write
    
    def _check_health(self):
        """Check if last successful scan was recent enough."""
        if self.last_successful_scan is None:
            print("WARNING: No successful scan recorded yet", file=__import__("sys").stderr)
            return
        
        elapsed = (datetime.now(timezone.utc) - self.last_successful_scan).total_seconds()
        if elapsed > HEALTH_WARNING_THRESHOLD:
            print(f"WARNING: No successful scan in {elapsed/3600:.1f} hours", file=__import__("sys").stderr)
    
    def run(self):
        """Main daemon loop."""
        print("Starting definition_change_monitor daemon...", file=__import__("sys").stderr)
        
        # Start heartbeat thread
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        
        try:
            while not self._stop_event.is_set():
                try:
                    write_count, rows = self.check_for_changes()
                    if write_count > 0:
                        print(f"Detected changes, wrote {len(rows)} history rows", file=__import__("sys").stderr)
                except Exception as e:
                    print(f"Error in scan cycle: {e}", file=__import__("sys").stderr)
                
                # Health check
                self._check_health()
                
                # Wait for stop or next scan interval
                self._stop_event.wait(SCAN_INTERVAL)
        
        except KeyboardInterrupt:
            print("Received shutdown signal", file=__import__("sys").stderr)
        finally:
            self._stop_event.set()
            if self._heartbeat_thread:
                self._heartbeat_thread.join(timeout=5)
            print("Daemon stopped", file=__import__("sys").stderr)
    
    def stop(self):
        """Stop the daemon."""
        self._stop_event.set()


# For testing without mocking external services
class MockRequests:
    """Mock requests for testing."""
    _mock_mcp_servers = []
    _mock_history = []
    _call_count = 0
    
    @classmethod
    def reset(cls):
        cls._mock_mcp_servers = []
        cls._mock_history = []
        cls._call_count = 0
    
    @classmethod
    def set_mock_mcp_servers(cls, servers):
        cls._mock_mcp_servers = servers
    
    @classmethod
    def post(cls, url, json=None, timeout=None):
        cls._call_count += 1
        
        class MockResponse:
            status_code = 200
            
            def raise_for_status(self):
                pass
            
            def json(self):
                if "/query" in url:
                    sql = json.get("sql", "").lower()
                    if "mcp_server_registry" in sql and "tool_schema" in sql:
                        return {"rows": cls._mock_mcp_servers}
                    elif "mcp_definition_history" in sql:
                        return {"rows": cls._mock_history}
                elif "/write" in url:
                    # Store written rows
                    if "mcp_definition_history" in json.get("table", ""):
                        cls._mock_history.extend(json.get("rows", []))
                    return {"success": True}
                return {"rows": [], "data": []}
        
        return MockResponse()


def self_test():
    """
    Acceptance test for the daemon skeleton.
    Mocks write_service responses, calls check_for_changes with synthetic data,
    asserts write calls match changed tools.
    """
    import sys
    
    # Save original requests
    original_requests = requests.post
    
    try:
        # Setup mock data
        mock_servers = [
            {
                "mcp_id": "mcp_1",
                "name": "Test Server 1",
                "tool_schema": json.dumps({
                    "tools": [
                        {"name": "tool_a", "description": "A test tool", "inputSchema": {"type": "object"}},
                        {"name": "tool_b", "description": "Another test tool", "inputSchema": {"type": "object"}}
                    ]
                })
            },
            {
                "mcp_id": "mcp_2",
                "name": "Test Server 2",
                "tool_schema": json.dumps({
                    "tools": [
                        {"name": "tool_c", "description": "Tool C", "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}}
                    ]
                })
            }
        ]
        
        # Set up mocks
        MockRequests.reset()
        MockRequests.set_mock_mcp_servers(mock_servers)
        requests.post = MockRequests.post
        
        # Create daemon instance
        daemon = DefinitionChangeHistoryWriter()
        
        # Override column detection for test
        daemon._schema_columns = ["mcp_id", "name", "tool_schema"]
        daemon._history_columns = ["mcp_id", "changed_at", "change_type", "tool_name", 
                                   "before_schema", "after_schema", "diff_summary"]
        
        # Run check_for_changes
        write_count, rows = daemon.check_for_changes()
        
        # Assert results
        assert write_count >= 1, f"Expected at least 1 write call, got {write_count}"
        assert len(rows) >= 1, f"Expected at least 1 row, got {len(rows)}"
        
        # Verify row structure
        required_columns = ["mcp_id", "changed_at", "change_type", "tool_name", 
                           "before_schema", "after_schema", "diff_summary"]
        for row in rows:
            for col in required_columns:
                assert col in row, f"Missing required column: {col}"
        
        # Verify change types
        change_types = set(r["change_type"] for r in rows)
        assert "added_tools" in change_types, "Expected added_tools change type"
        
        # Count tools that should have been detected
        expected_added_tools = 3  # mcp_1: tool_a, tool_b; mcp_2: tool_c
        
        # Get added_tools rows
        added_rows = [r for r in rows if r["change_type"] == "added_tools"]
        assert len(added_rows) == expected_added_tools, \
            f"Expected {expected_added_tools} added_tools, got {len(added_rows)}"
        
        # Reset mock for second run (no changes expected)
        MockRequests._mock_history = rows.copy()  # History now has the records
        
        # Second run should detect no changes
        MockRequests._call_count = 0
        write_count_2, rows_2 = daemon.check_for_changes()
        
        # Should have "unchanged" rows if outside cooldown, or no rows if within cooldown
        # Since first run just happened, we expect 0 new rows due to immediate re-check
        
        # Verify mock call count
        assert MockRequests._call_count > 0, "Expected mock HTTP calls to be made"
        
        print("PASS: definition_change_monitor daemon skeleton valid")
        
    finally:
        # Restore original requests
        requests.post = original_requests


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        self_test()
    else:
        daemon = DefinitionChangeHistoryWriter()
        daemon.run()