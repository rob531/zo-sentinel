#!/usr/bin/env python3
"""
snow_outbound_notification_writer.py

Daemon that polls for new mcp_decisions with status='APPROVED' or 'CONDITIONAL'
and writes outbound ServiceNow notification records to snow_outbound_notifications table.
"""

import os
import sys
import json
import time
import signal
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests


# Configuration
WRITE_SERVICE_URL = "http://localhost:8772"
SNOW_NOTIFY_INTERVAL = int(os.environ.get("SNOW_NOTIFY_INTERVAL", "30"))
SNOW_ENABLED = os.environ.get("SNOW_ENABLED", "true").lower() == "true"
SNOW_INSTANCE_URL = os.environ.get("SNOW_INSTANCE_URL", "")
HEARTBEAT_INTERVAL = 60  # seconds
REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
BACKOFF_BASE = 2  # exponential backoff base


class ShutdownRequested(Exception):
    """Raised when daemon receives SIGTERM."""
    pass


def load_config() -> dict:
    """Load configuration from environment or sentinel_config.json."""
    config = {
        "SNOW_ENABLED": SNOW_ENABLED,
        "SNOW_INSTANCE_URL": SNOW_INSTANCE_URL,
        "SNOW_NOTIFY_INTERVAL": SNOW_NOTIFY_INTERVAL,
    }
    
    # Try to load from sentinel_config.json if it exists
    config_paths = [
        "sentinel_config.json",
        "/etc/zo-sentinel/sentinel_config.json",
        os.path.join(os.path.dirname(__file__), "sentinel_config.json"),
    ]
    
    for config_path in config_paths:
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    file_config = json.load(f)
                    config.update({k: v for k, v in file_config.items() if k in config})
            except (json.JSONDecodeError, IOError):
                pass
    
    return config


def send_heartbeat(config: dict) -> bool:
    """Send heartbeat to service_health."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/health/heartbeat",
            json={
                "service": "snow_outbound_notification_writer",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            },
            timeout=REQUEST_TIMEOUT,
        )
        return response.status_code < 400
    except requests.RequestException:
        return False


def call_write_service(action: str, payload: dict) -> tuple[bool, Optional[dict]]:
    """Call write_service with exponential backoff on 5xx errors."""
    url = f"{WRITE_SERVICE_URL}/{action}"
    
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            
            if response.status_code < 500:
                if response.status_code < 400:
                    try:
                        return True, response.json()
                    except json.JSONDecodeError:
                        return True, {"raw": response.text}
                else:
                    # 4xx error - don't retry
                    return False, {"error": f"Client error: {response.status_code}", "detail": response.text}
            
            # 5xx error - retry with backoff
            if attempt < MAX_RETRIES - 1:
                backoff_time = BACKOFF_BASE ** attempt
                time.sleep(backoff_time)
                
        except requests.Timeout:
            if attempt < MAX_RETRIES - 1:
                backoff_time = BACKOFF_BASE ** attempt
                time.sleep(backoff_time)
        except requests.RequestException as e:
            return False, {"error": str(e)}
    
    return False, {"error": "Max retries exceeded"}


def fetch_pending_decisions(limit: int = 50) -> list[dict]:
    """
    Query mcp_decisions for pending notifications.
    
    Returns decisions with status IN ('APPROVED','CONDITIONAL') AND snow_notified = False.
    """
    payload = {
        "table": "mcp_decisions",
        "action": "select",
        "where": "status IN ('APPROVED', 'CONDITIONAL') AND snow_notified = false",
        "limit": limit,
        "columns": ["id", "mcp_server_id", "decision", "decided_by", "decided_at", "expiry_date", "conditions"],
    }
    
    success, result = call_write_service("query", payload)
    
    if success and result:
        if isinstance(result, dict) and "rows" in result:
            return result["rows"]
        elif isinstance(result, list):
            return result
    
    return []


def write_notification(record: dict) -> bool:
    """
    Write notification record to snow_outbound_notifications via write_service.
    
    Returns True on success, False on failure.
    """
    payload = {
        "table": "snow_outbound_notifications",
        "action": "insert",
        "row": record,
    }
    
    success, _ = call_write_service("write", payload)
    return success


def mark_decision_notified(decision_id: str) -> bool:
    """Mark a mcp_decision as snow_notified=True."""
    payload = {
        "table": "mcp_decisions",
        "action": "update",
        "where": f"id = '{decision_id}'",
        "set": {"snow_notified": True},
    }
    
    success, _ = call_write_service("write", payload)
    return success


def build_notification_payload(decision: dict, config: dict) -> dict:
    """Build the notification payload from a decision record."""
    notification_type = "MCP_APPROVAL" if decision.get("decision") == "APPROVED" else "MCP_CONDITIONAL_APPROVAL"
    
    payload = {
        "mcp_server_id": decision.get("mcp_server_id"),
        "decision": decision.get("decision"),
        "decided_by": decision.get("decided_by"),
        "decided_at": decision.get("decided_at"),
        "expiry_date": decision.get("expiry_date"),
        "conditions": decision.get("conditions"),
        "snow_instance_url": config.get("SNOW_INSTANCE_URL", ""),
    }
    
    return {
        "id": str(uuid.uuid4()),
        "mcp_server_id": decision.get("mcp_server_id"),
        "notification_type": notification_type,
        "payload_json": json.dumps(payload),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sent_at": None,
        "status": "PENDING",
        "error_message": None,
    }


def process_decisions(config: dict) -> tuple[int, int]:
    """
    Process pending decisions and write notifications.
    
    Returns (processed_count, success_count).
    """
    decisions = fetch_pending_decisions(limit=50)
    
    processed = 0
    succeeded = 0
    
    for decision in decisions:
        decision_id = decision.get("id")
        if not decision_id:
            continue
        
        notification = build_notification_payload(decision, config)
        
        if write_notification(notification):
            if mark_decision_notified(decision_id):
                succeeded += 1
            else:
                # Write succeeded but marking notified failed
                # Log warning but count as processed
                pass
        
        processed += 1
    
    return processed, succeeded


def check_pending(config: Optional[dict] = None) -> tuple[float, dict]:
    """
    Self-test function: check pending decisions and return score with evidence.
    
    Returns (score: float in 0..100, evidence: dict).
    """
    if config is None:
        config = load_config()
    
    evidence = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "snow_enabled": config.get("SNOW_ENABLED", True),
        "config": config,
        "decisions_found": 0,
        "decisions_processed": 0,
        "notifications_written": 0,
        "errors": [],
    }
    
    try:
        decisions = fetch_pending_decisions(limit=50)
        evidence["decisions_found"] = len(decisions)
        
        for decision in decisions:
            decision_id = decision.get("id")
            if not decision_id:
                continue
            
            evidence["decisions_processed"] += 1
            
            notification = build_notification_payload(decision, config)
            
            if write_notification(notification):
                evidence["notifications_written"] += 1
                mark_decision_notified(decision_id)
        
        # Calculate score based on processing
        if evidence["decisions_found"] == 0:
            score = 100.0  # No pending = all good
        else:
            score = (evidence["notifications_written"] / evidence["decisions_processed"]) * 100 if evidence["decisions_processed"] > 0 else 0.0
        
        evidence["score"] = score
        
    except Exception as e:
        evidence["errors"].append(str(e))
        score = 0.0
    
    return score, evidence


def setup_signal_handlers():
    """Setup SIGTERM handler for graceful shutdown."""
    def sigterm_handler(signum, frame):
        raise ShutdownRequested()
    
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigterm_handler)


def run():
    """
    Main daemon loop.
    
    Polls for pending MCP decisions and writes notifications to snow_outbound_notifications.
    Sends heartbeat every 60 seconds.
    """
    config = load_config()
    
    setup_signal_handlers()
    
    last_heartbeat = time.time()
    last_poll = 0
    
    print(f"[snow_outbound_notification_writer] Starting daemon...")
    print(f"[snow_outbound_notification_writer] SNOW_ENABLED={config.get('SNOW_ENABLED')}")
    print(f"[snow_outbound_notification_writer] Poll interval={SNOW_NOTIFY_INTERVAL}s")
    
    try:
        while True:
            current_time = time.time()
            
            # Send heartbeat if needed
            if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat(config)
                last_heartbeat = current_time
            
            # Poll for decisions if enabled
            if config.get("SNOW_ENABLED", True):
                if current_time - last_poll >= SNOW_NOTIFY_INTERVAL:
                    processed, succeeded = process_decisions(config)
                    if processed > 0:
                        print(f"[snow_outbound_notification_writer] Processed: {processed}, Succeeded: {succeeded}")
                    last_poll = current_time
            
            # Sleep in small increments to be responsive to signals
            time.sleep(1)
            
    except ShutdownRequested:
        print("[snow_outbound_notification_writer] Received shutdown signal, exiting cleanly...")
        return


# Mock write_service for self-testing
class MockWriteService:
    """Mock write_service for testing purposes."""
    
    def __init__(self):
        self.mcp_decisions = {}
        self.snow_outbound_notifications = []
        self.next_decision_id = 1
    
    def reset(self):
        """Reset all data."""
        self.mcp_decisions = {}
        self.snow_outbound_notifications = []
        self.next_decision_id = 1
    
    def add_decision(self, decision: dict) -> str:
        """Add a mock MCP decision."""
        decision_id = decision.get("id") or f"decision-{self.next_decision_id}"
        self.next_decision_id += 1
        decision["id"] = decision_id
        self.mcp_decisions[decision_id] = dict(decision)
        return decision_id
    
    def handle_request(self, action: str, payload: dict) -> tuple[int, dict]:
        """Handle a mock request."""
        if action == "query":
            # Return decisions matching criteria
            table = payload.get("table", "")
            where = payload.get("where", "")
            limit = payload.get("limit", 50)
            
            if table == "mcp_decisions":
                # Parse where clause
                results = []
                for decision_id, decision in self.mcp_decisions.items():
                    status = decision.get("status", "")
                    snow_notified = decision.get("snow_notified", False)
                    
                    # Check if matches criteria
                    if "APPROVED" in status and not snow_notified:
                        results.append(decision)
                    elif "CONDITIONAL" in status and not snow_notified:
                        results.append(decision)
                    
                    if len(results) >= limit:
                        break
                
                return 200, {"rows": results}
            
            return 404, {"error": "Table not found"}
        
        elif action == "write":
            table = payload.get("table", "")
            write_action = payload.get("action", "")
            
            if table == "snow_outbound_notifications" and write_action == "insert":
                row = payload.get("row", {})
                self.snow_outbound_notifications.append(dict(row))
                return 200, {"success": True, "id": row.get("id")}
            
            elif table == "mcp_decisions" and write_action == "update":
                where = payload.get("where", "")
                set_clause = payload.get("set", {})
                
                # Parse decision ID from where clause
                # Format: id = 'decision-1'
                import re
                match = re.search(r"id\s*=\s*'([^']+)'", where)
                if match:
                    decision_id = match.group(1)
                    if decision_id in self.mcp_decisions:
                        self.mcp_decisions[decision_id].update(set_clause)
                        return 200, {"success": True}
                
                return 404, {"error": "Decision not found"}
            
            return 404, {"error": "Unknown write action"}
        
        elif action == "health" and payload.get("service"):
            return 200, {"status": "ok"}
        
        return 404, {"error": "Unknown action"}


# Global mock service for testing
_mock_service = MockWriteService()


def run_self_test():
    """Run self-test with mock write_service."""
    from unittest.mock import patch
    import re
    
    # Setup mock decisions
    _mock_service.reset()
    
    test_decisions = [
        {
            "id": "test-decision-1",
            "mcp_server_id": "server-1",
            "decision": "APPROVED",
            "decided_by": "analyst@example.com",
            "decided_at": "2024-01-15T10:00:00Z",
            "expiry_date": "2024-02-15T10:00:00Z",
            "conditions": None,
            "snow_notified": False,
        },
        {
            "id": "test-decision-2",
            "mcp_server_id": "server-2",
            "decision": "CONDITIONAL",
            "decided_by": "analyst@example.com",
            "decided_at": "2024-01-15T11:00:00Z",
            "expiry_date": "2024-02-15T11:00:00Z",
            "conditions": "Restricted to specific IPs",
            "snow_notified": False,
        },
    ]
    
    for decision in test_decisions:
        _mock_service.add_decision(decision)
    
    # Mock requests.post to use our mock service
    original_post = requests.post
    
    def mock_post(url, json=None, timeout=None):
        """Mock requests.post for testing."""
        class MockResponse:
            def __init__(self, status_code, json_data):
                self.status_code = status_code
                self._json_data = json_data
            
            def json(self):
                return self._json_data
        
        # Parse URL to determine action
        if "/health/heartbeat" in url:
            return MockResponse(200, {"status": "ok"})
        elif "/query" in url:
            action = "query"
        elif "/write" in url:
            action = "write"
        else:
            return MockResponse(404, {"error": "Unknown endpoint"})
        
        status_code, response_data = _mock_service.handle_request(action, json or {})
        return MockResponse(status_code, response_data)
    
    # Run test
    config = {
        "SNOW_ENABLED": True,
        "SNOW_INSTANCE_URL": "https://test.service-now.com",
        "SNOW_NOTIFY_INTERVAL": 30,
    }
    
    with patch('requests.post', side_effect=mock_post):
        score, evidence = check_pending(config)
    
    # Assertions
    assert isinstance(score, float), f"Score should be float, got {type(score)}"
    assert 0 <= score <= 100, f"Score should be 0-100, got {score}"
    assert isinstance(evidence, dict), f"Evidence should be dict, got {type(evidence)}"
    assert "decisions_found" in evidence, "Evidence missing decisions_found"
    assert "notifications_written" in evidence, "Evidence missing notifications_written"
    assert evidence["decisions_found"] == 2, f"Should find 2 decisions, got {evidence['decisions_found']}"
    assert evidence["notifications_written"] == 2, f"Should write 2 notifications, got {evidence['notifications_written']}"
    
    # Verify notifications were written
    assert len(_mock_service.snow_outbound_notifications) == 2, \
        f"Should have 2 notifications, got {len(_mock_service.snow_outbound_notifications)}"
    
    # Verify decisions were marked as notified
    for decision_id in ["test-decision-1", "test-decision-2"]:
        assert _mock_service.mcp_decisions[decision_id].get("snow_notified") == True, \
            f"Decision {decision_id} should be marked as notified"
    
    print(f"PASS: check_pending() returned score={score}, evidence={evidence}")
    return True


def run_daemon_smoke_test():
    """Run daemon smoke test with SIGTERM after 1 second."""
    import threading
    
    # Create mock decisions
    _mock_service.reset()
    _mock_service.add_decision({
        "id": "smoke-test-1",
        "mcp_server_id": "server-smoke",
        "decision": "APPROVED",
        "decided_by": "test@test.com",
        "decided_at": "2024-01-15T12:00:00Z",
        "expiry_date": "2024-02-15T12:00:00Z",
        "conditions": None,
        "snow_notified": False,
    })
    
    # Mock requests
    def mock_post(url, json=None, timeout=None):
        class MockResponse:
            def __init__(self, status_code, json_data):
                self.status_code = status_code
                self._json_data = json_data
            
            def json(self):
                return self._json_data
        
        if "/health/heartbeat" in url:
            return MockResponse(200, {"status": "ok"})
        elif "/query" in url:
            action = "query"
        elif "/write" in url:
            action = "write"
        else:
            return MockResponse(404, {"error": "Unknown endpoint"})
        
        status_code, response_data = _mock_service.handle_request(action, json or {})
        return MockResponse(status_code, response_data)
    
    def send_term():
        """Send SIGTERM after 1 second."""
        time.sleep(1)
        os.kill(os.getpid(), signal.SIGTERM)
    
    with patch('requests.post', side_effect=mock_post):
        with patch('requests.get', side_effect=mock_post):
            term_thread = threading.Thread(target=send_term)
            term_thread.daemon = True
            term_thread.start()
            
            try:
                run()
                print("PASS: run() exited cleanly after SIGTERM")
                return True
            except Exception as e:
                print(f"FAIL: run() raised {type(e).__name__}: {e}")
                return False


if __name__ == "__main__":
    # Run self-test first
    print("Running self-test...")
    
    try:
        success = run_self_test()
    except Exception as e:
        print(f"FAIL: Self-test raised {type(e).__name__}: {e}")
        sys.exit(1)
    
    if not success:
        sys.exit(1)
    
    # Run daemon smoke test
    print("\nRunning daemon smoke test...")
    
    try:
        success = run_daemon_smoke_test()
    except Exception as e:
        print(f"FAIL: Daemon smoke test raised {type(e).__name__}: {e}")
        sys.exit(1)
    
    if success:
        print("\nAll tests PASSED")
        sys.exit(0)
    else:
        sys.exit(1)