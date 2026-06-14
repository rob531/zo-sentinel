#!/usr/bin/env python3
"""
Definition Change Detector Daemon

Detects version/schema changes in the MCP server registry and writes them to
mcp_definition_history. Uses in-memory snapshots for idempotency.
"""

import json
import time
import hashlib
import threading
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Service configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 60  # seconds
CYCLE_INTERVAL = 300  # seconds (5 minutes)
MAX_RETRIES = 3
BACKOFF_FACTOR = 2


class DefinitionChangeDetector:
    """
    Daemon that tracks MCP server definition changes.
    
    Maintains an in-memory snapshot of server hashes and writes
    change records when differences are detected.
    """
    
    def __init__(self):
        self.snapshot: Dict[str, Dict[str, str]] = {}  # server_id -> field hashes
        self.logger = logger
        self._cycle_running = False
    
    def _call_service(
        self,
        url: str,
        payload: Dict[str, Any],
        retries: int = MAX_RETRIES
    ) -> Optional[Dict[str, Any]]:
        """
        Make HTTP call to write_service with exponential backoff.
        """
        backoff = 1
        for attempt in range(retries):
            try:
                response = requests.post(url, json=payload, timeout=30)
                if response.status_code < 500:
                    return response.json()
                self.logger.warning(
                    f"Attempt {attempt + 1}/{retries} failed with status {response.status_code}"
                )
            except requests.exceptions.Timeout:
                self.logger.warning(f"Attempt {attempt + 1}/{retries} timed out")
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Attempt {attempt + 1}/{retries} failed: {e}")
            
            if attempt < retries - 1:
                time.sleep(backoff)
                backoff *= BACKOFF_FACTOR
        
        self.logger.error(f"All {retries} attempts failed for {url}")
        return None
    
    def _fetch_servers(self) -> List[Dict[str, Any]]:
        """
        Fetch all servers from mcp_server_registry using schema introspection.
        """
        # First, introspect the table to get column names (user-supplied values from schema)
        schema_payload = {
            "table": "mcp_server_registry",
            "query": "SELECT * FROM mcp_server_registry LIMIT 0"
        }
        schema_result = self._call_service(QUERY_SERVICE_URL, schema_payload)
        
        if not schema_result or schema_result.get("status") != "success":
            self.logger.error("Failed to introspect mcp_server_registry schema")
            return []
        
        columns = schema_result.get("columns", [])
        if not columns:
            self.logger.warning("No columns returned from schema introspection")
            return []
        
        # Build query using introspected column names
        column_list = ", ".join(columns)
        query = f"SELECT {column_list} FROM mcp_server_registry"
        
        payload = {
            "table": "mcp_server_registry",
            "query": query
        }
        result = self._call_service(QUERY_SERVICE_URL, payload)
        
        if result and result.get("status") == "success":
            return result.get("rows", [])
        return []
    
    def _compute_hash(self, data: Any) -> str:
        """
        Compute deterministic hash for data.
        """
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()
    
    def _get_server_state(self, server: Dict[str, Any]) -> Dict[str, str]:
        """
        Extract relevant fields and compute their hashes.
        """
        return {
            "version": self._compute_hash(server.get("version")),
            "tool_schema": self._compute_hash(server.get("tool_schema")),
            "description": self._compute_hash(server.get("description"))
        }
    
    def _detect_changes(
        self,
        server: Dict[str, Any],
        previous_state: Optional[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        """
        Detect what changed between current and previous state.
        """
        changes = []
        server_id = server["server_id"]
        current_state = self._get_server_state(server)
        now = datetime.utcnow().isoformat()
        
        if previous_state is None:
            # Newly discovered server
            changes.append({
                "server_id": server_id,
                "change_type": "newly_discovered",
                "old_value": None,
                "new_value": json.dumps({
                    "version": server.get("version"),
                    "tool_schema": server.get("tool_schema"),
                    "description": server.get("description")
                }),
                "changed_at": now
            })
        else:
            # Check each field for changes
            change_type_map = {
                "version": "version_bump",
                "tool_schema": "schema_edit",
                "description": "description_edit"
            }
            
            for field, change_type in change_type_map.items():
                if current_state[field] != previous_state[field]:
                    changes.append({
                        "server_id": server_id,
                        "change_type": change_type,
                        "old_value": json.dumps({"value": server.get(field)}),
                        "new_value": json.dumps({"value": server.get(field)}),
                        "changed_at": now
                    })
        
        return changes, current_state
    
    def _write_changes(self, changes: List[Dict[str, Any]]) -> bool:
        """
        Write change rows to mcp_definition_history.
        """
        if not changes:
            return True
        
        payload = {
            "table": "mcp_definition_history",
            "operation": "insert",
            "rows": changes
        }
        
        result = self._call_service(WRITE_SERVICE_URL, payload)
        if result and result.get("status") == "success":
            self.logger.info(f"Wrote {len(changes)} change(s) to mcp_definition_history")
            return True
        return False
    
    def _heartbeat(self):
        """
        Send heartbeat to service_health table every HEARTBEAT_INTERVAL seconds.
        Runs in a separate thread.
        """
        while True:
            payload = {
                "table": "service_health",
                "operation": "insert",
                "rows": [{
                    "service": "definition_change_detector",
                    "last_heartbeat": datetime.utcnow().isoformat()
                }]
            }
            
            backoff = 1
            for i in range(MAX_RETRIES):
                try:
                    response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
                    if response.status_code < 500:
                        break
                except Exception as e:
                    self.logger.warning(f"Heartbeat attempt {i+1} failed: {e}")
                    if i < MAX_RETRIES - 1:
                        time.sleep(backoff)
                        backoff *= BACKOFF_FACTOR
            
            time.sleep(HEARTBEAT_INTERVAL)
    
    def _run_cycle(self):
        """
        Run one detection cycle: fetch servers, diff against snapshot, write changes.
        """
        self._cycle_running = True
        try:
            servers = self._fetch_servers()
            current_ids = {s["server_id"] for s in servers}
            
            # Detect removed servers
            for server_id in list(self.snapshot.keys()):
                if server_id not in current_ids:
                    previous_state = self.snapshot[server_id]
                    change = {
                        "server_id": server_id,
                        "change_type": "server_removed",
                        "old_value": json.dumps(previous_state),
                        "new_value": None,
                        "changed_at": datetime.utcnow().isoformat()
                    }
                    self._write_changes([change])
                    del self.snapshot[server_id]
            
            changes = []
            for server in servers:
                server_id = server["server_id"]
                previous_state = self.snapshot.get(server_id)
                
                detected_changes, current_state = self._detect_changes(server, previous_state)
                changes.extend(detected_changes)
                self.snapshot[server_id] = current_state
            
            if changes:
                self._write_changes(changes)
            
            self.logger.info(f"Cycle complete. Processed {len(servers)} server(s), "
                           f"detected {len(changes)} change(s)")
        finally:
            self._cycle_running = False
    
    def run(self):
        """
        Main daemon loop.
        """
        self.logger.info("Definition Change Detector starting...")
        
        # Start heartbeat in a separate thread
        heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True)
        heartbeat_thread.start()
        self.logger.info("Heartbeat thread started")
        
        last_cycle_time = time.time()
        
        try:
            while True:
                now = time.time()
                
                # Run detection cycle on interval
                if now - last_cycle_time >= CYCLE_INTERVAL:
                    if not self._cycle_running:
                        self._run_cycle()
                        last_cycle_time = now
                    else:
                        self.logger.warning("Previous cycle still running, skipping this cycle")
                
                time.sleep(10)  # Sleep in small increments for responsive shutdown
                
        except KeyboardInterrupt:
            self.logger.info("Definition Change Detector shutting down...")
    
    def self_test(self) -> bool:
        """
        Self-test: verify mcp_definition_history is queryable and has expected columns.
        Returns True if test passes.
        """
        self.logger.info("Running self-test...")
        
        # Test 1: Verify table exists and is queryable
        payload = {
            "table": "mcp_definition_history",
            "query": "SELECT * FROM mcp_definition_history LIMIT 1"
        }
        result = self._call_service(QUERY_SERVICE_URL, payload)
        
        if not result or result.get("status") != "success":
            print("FAIL: mcp_definition_history table not queryable")
            return False
        
        # Test 2: Verify expected columns exist
        columns = result.get("columns", [])
        expected_columns = {
            "change_id", "server_id", "change_type",
            "old_value", "new_value", "changed_at"
        }
        actual_columns = set(columns)
        
        missing_columns = expected_columns - actual_columns
        if missing_columns:
            print(f"FAIL: Missing columns: {missing_columns}")
            return False
        
        print("PASS")
        return True


if __name__ == '__main__':
    detector = DefinitionChangeDetector()
    success = detector.self_test()
    if not success:
        exit(1)
    detector.run()