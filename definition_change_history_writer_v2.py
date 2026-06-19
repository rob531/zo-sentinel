"""
definition_change_history_writer_v2.py
=====================================
Writes to mcp_definition_history table when server definition changes.
Compares current mcp_server_registry.tool_definitions against stored fingerprints.
If diff detected, writes to mcp_definition_history with timestamp, old_hash, new_hash, changed_fields.
Triggered by mcp_scanner or signal_analyser.
Writes via write_service :8772, never direct DuckDB.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import requests

from .definition_fingerprint import DefinitionFingerprint
from .write_service_client import WriteServiceClient

logger = logging.getLogger(__name__)

# Write service endpoint
WRITE_SERVICE_URL = "http://localhost:8772"


class DefinitionChangeHistoryWriter:
    """Tracks and records changes to MCP server tool definitions."""

    def __init__(self, write_service_url: str = WRITE_SERVICE_URL):
        self.write_service_url = write_service_url
        self.write_client = WriteServiceClient(write_service_url)

    def compute_definition_hash(self, tool_definitions: dict[str, Any]) -> str:
        """
        Compute a stable hash of tool definitions.
        
        Args:
            tool_definitions: Dictionary of tool definitions
            
        Returns:
            SHA256 hash string
        """
        # Normalize and sort for stable hashing
        normalized = json.dumps(tool_definitions, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode()).hexdigest()

    def compute_changed_fields(
        self, 
        old_definitions: dict[str, Any] | None, 
        new_definitions: dict[str, Any]
    ) -> list[str]:
        """
        Identify which fields have changed between definitions.
        
        Args:
            old_definitions: Previous tool definitions (or None if new)
            new_definitions: Current tool definitions
            
        Returns:
            List of field names that have changed
        """
        if old_definitions is None:
            return ["*new_definition*"]
        
        changed_fields = []
        
        all_keys = set(old_definitions.keys()) | set(new_definitions.keys())
        
        for key in all_keys:
            old_value = old_definitions.get(key)
            new_value = new_definitions.get(key)
            
            if old_value != new_value:
                # Determine specific changed sub-fields
                if isinstance(old_value, dict) and isinstance(new_value, dict):
                    sub_changed = self._find_dict_changes(
                        old_value, 
                        new_value, 
                        prefix=key
                    )
                    changed_fields.extend(sub_changed)
                else:
                    changed_fields.append(key)
        
        return changed_fields if changed_fields else ["*no_visible_changes*"]

    def _find_dict_changes(
        self, 
        old_dict: dict, 
        new_dict: dict, 
        prefix: str = ""
    ) -> list[str]:
        """Recursively find changes in nested dictionaries."""
        changes = []
        all_keys = set(old_dict.keys()) | set(new_dict.keys())
        
        for key in all_keys:
            full_path = f"{prefix}.{key}" if prefix else key
            old_val = old_dict.get(key)
            new_val = new_dict.get(key)
            
            if old_val != new_val:
                if isinstance(old_val, dict) and isinstance(new_val, dict):
                    changes.extend(self._find_dict_changes(old_val, new_val, full_path))
                else:
                    changes.append(full_path)
        
        return changes

    def check_and_record_changes(
        self,
        server_id: str,
        current_definitions: dict[str, Any],
        stored_fingerprint: DefinitionFingerprint | None
    ) -> dict[str, Any] | None:
        """
        Check for definition changes and record to history if detected.
        
        Args:
            server_id: The MCP server identifier
            current_definitions: Current tool definitions from registry
            stored_fingerprint: Previously stored fingerprint (if any)
            
        Returns:
            History record if change detected, None otherwise
        """
        current_hash = self.compute_definition_hash(current_definitions)
        
        # No previous fingerprint means new entry, not a change
        if stored_fingerprint is None:
            logger.debug(f"Server {server_id}: No stored fingerprint, skipping change detection")
            return None
        
        # Check if hash matches stored
        if current_hash == stored_fingerprint.definition_hash:
            logger.debug(f"Server {server_id}: No change detected (hash matches)")
            return None
        
        # Change detected - compute details
        old_definitions = stored_fingerprint.raw_definitions
        changed_fields = self.compute_changed_fields(old_definitions, current_definitions)
        
        timestamp = datetime.now(timezone.utc).isoformat()
        
        history_record = {
            "server_id": server_id,
            "timestamp": timestamp,
            "old_hash": stored_fingerprint.definition_hash,
            "new_hash": current_hash,
            "changed_fields": changed_fields,
            "definition_snapshot": current_definitions
        }
        
        logger.info(
            f"Change detected for server {server_id}: "
            f"{len(changed_fields)} field(s) changed"
        )
        
        return history_record

    def write_to_history(
        self,
        server_id: str,
        current_definitions: dict[str, Any],
        stored_fingerprint: DefinitionFingerprint | None
    ) -> bool:
        """
        Check for changes and write to mcp_definition_history via write_service.
        
        Args:
            server_id: The MCP server identifier
            current_definitions: Current tool definitions
            stored_fingerprint: Previously stored fingerprint
            
        Returns:
            True if change was detected and recorded, False otherwise
        """
        history_record = self.check_and_record_changes(
            server_id,
            current_definitions,
            stored_fingerprint
        )
        
        if history_record is None:
            return False
        
        # Write via write_service :8772
        try:
            self._write_via_service(history_record)
            logger.info(f"Successfully recorded definition change for server {server_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to write history via write_service: {e}")
            raise

    def _write_via_service(self, history_record: dict[str, Any]) -> None:
        """Write history record via write_service endpoint."""
        endpoint = f"{self.write_service_url}/mcp_definition_history"
        
        response = requests.post(
            endpoint,
            json=history_record,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        response.raise_for_status()
        
        result = response.json()
        if not result.get("success", False):
            raise RuntimeError(f"Write service rejected record: {result}")


def create_writer() -> DefinitionChangeHistoryWriter:
    """Factory function to create a configured writer instance."""
    return DefinitionChangeHistoryWriter()


# Standalone function for use by mcp_scanner or signal_analyser
def record_definition_change(
    server_id: str,
    current_definitions: dict[str, Any],
    stored_fingerprint: DefinitionFingerprint | None,
    write_service_url: str = WRITE_SERVICE_URL
) -> bool:
    """
    Convenience function to record a definition change.
    
    Can be called by mcp_scanner or signal_analyser.
    
    Args:
        server_id: The MCP server identifier
        current_definitions: Current tool definitions
        stored_fingerprint: Previously stored fingerprint (if any)
        write_service_url: Write service endpoint URL
        
    Returns:
        True if change was detected and recorded
    """
    writer = DefinitionChangeHistoryWriter(write_service_url)
    return writer.write_to_history(server_id, current_definitions, stored_fingerprint)