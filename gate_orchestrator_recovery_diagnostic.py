#!/usr/bin/env python3
"""
gate_orchestrator_recovery_diagnostic.py

Diagnostic utility to investigate why gate_orchestrator daemon is in error state.
Queries service_health via write_service HTTP API to extract error information
and classify the failure type.

IMPORTANT: This is a READ-ONLY diagnostic utility - does NOT rebuild or modify
gate_orchestrator.
"""

import json
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
QUERY_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
BACKOFF_FACTOR = 2  # exponential backoff multiplier

# Error classification keywords
INIT_KEYWORDS = ["init", "config", "load", "startup"]
ROUTING_KEYWORDS = ["route", "dispatch", "route_signal", "send"]
EXTERNAL_KEYWORDS = ["timeout", "connection", "refused", "external", "dependency"]


def classify_error(error_message: str) -> Tuple[str, List[str]]:
    """
    Classify error based on keywords in error message.

    Args:
        error_message: The error message string to analyze

    Returns:
        Tuple of (classification, matched_keywords)
    """
    error_lower = error_message.lower()

    # Check INIT keywords
    init_matches = [kw for kw in INIT_KEYWORDS if kw in error_lower]
    if init_matches:
        return ("INIT", init_matches)

    # Check ROUTING keywords
    routing_matches = [kw for kw in ROUTING_KEYWORDS if kw in error_lower]
    if routing_matches:
        return ("ROUTING", routing_matches)

    # Check EXTERNAL keywords
    external_matches = [kw for kw in EXTERNAL_KEYWORDS if kw in error_lower]
    if external_matches:
        return ("EXTERNAL", external_matches)

    return ("UNKNOWN", [])


def get_possible_causes(classification: str) -> List[str]:
    """
    Get possible causes based on error classification.

    Args:
        classification: The error classification (INIT, ROUTING, EXTERNAL, UNKNOWN)

    Returns:
        List of possible cause descriptions
    """
    causes = {
        "INIT": [
            "Configuration file missing or malformed",
            "Required environment variables not set",
            "Dependencies not fully initialized",
            "Permission issues on startup",
            "Port binding conflict on startup"
        ],
        "ROUTING": [
            "Signal routing table corrupted",
            "Policy dispatch rules misconfigured",
            "Inter-daemon communication failure",
            "Message queue unavailable",
            "Routing policy not defined for signal type"
        ],
        "EXTERNAL": [
            "Database connection pool exhausted",
            "Downstream service timeout",
            "Network connectivity issues",
            "External API rate limiting",
            "Dependency service unavailable"
        ],
        "UNKNOWN": [
            "Unexpected error type encountered",
            "Error message format not recognized",
            "New error category not yet classified",
            "Multiple concurrent failures"
        ]
    }
    return causes.get(classification, causes["UNKNOWN"])


def get_suggested_remediation(classification: str) -> List[str]:
    """
    Get suggested remediation steps based on error classification.

    Args:
        classification: The error classification

    Returns:
        List of actionable remediation steps
    """
    remediation = {
        "INIT": [
            "Verify configuration files in /etc/gate_orchestrator/",
            "Check service logs for startup errors: journalctl -u gate_orchestrator",
            "Ensure all dependency services are running",
            "Verify file permissions on config and data directories",
            "Check for port conflicts: netstat -tlnp | grep 8772"
        ],
        "ROUTING": [
            "Review routing configuration in database",
            "Check inter-daemon connectivity with: ping <daemon_host>",
            "Verify policy dispatch rules are up to date",
            "Restart routing subsystem if available",
            "Check for network segmentation or firewall issues"
        ],
        "EXTERNAL": [
            "Check downstream service health endpoints",
            "Verify network access: curl -I http://<dependency>",
            "Review timeout configurations in settings",
            "Check database connection pool settings",
            "Examine recent changes to external dependencies"
        ],
        "UNKNOWN": [
            "Collect full error trace from logs",
            "Review recent configuration changes",
            "Check for multiple simultaneous failures",
            "Gather metrics for trend analysis",
            "Contact support with full diagnostic output"
        ]
    }
    return remediation.get(classification, remediation["UNKNOWN"])


def query_service_health() -> Optional[Dict[str, Any]]:
    """
    Query service_health table via write_service HTTP API.

    Returns:
        Dictionary with service record or None if not found

    Raises:
        SystemExit: If connection cannot be established
    """
    sql = (
        "SELECT service_name, status, last_heartbeat, meta "
        "FROM service_health "
        "WHERE service_name = 'gate_orchestrator' "
        "ORDER BY last_heartbeat DESC "
        "LIMIT 1"
    )

    payload = {
        "sql": sql,
        "target": "service_health"
    }

    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                WRITE_SERVICE_URL,
                json=payload,
                timeout=QUERY_TIMEOUT
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("results"):
                    return data["results"][0]
                return None
            else:
                raise Exception(f"HTTP {response.status_code}: {response.text}")

        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection to {WRITE_SERVICE_URL} refused"
            if attempt < MAX_RETRIES - 1:
                sleep_time = BACKOFF_FACTOR ** attempt
                time.sleep(sleep_time)
            continue

        except requests.exceptions.Timeout as e:
            last_error = f"Connection to {WRITE_SERVICE_URL} timed out"
            if attempt < MAX_RETRIES - 1:
                sleep_time = BACKOFF_FACTOR ** attempt
                time.sleep(sleep_time)
            continue

        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                sleep_time = BACKOFF_FACTOR ** attempt
                time.sleep(sleep_time)
            continue

    print(f"Error: {last_error}", file=sys.stderr)
    print("Failed to connect to write_service after retries", file=sys.stderr)
    raise SystemExit(1)


def run() -> bool:
    """
    Main entry point for the diagnostic utility.

    Returns:
        True if diagnostic completed successfully, False if service not found
    """
    timestamp = datetime.utcnow().isoformat() + "Z"

    print("=== GATE_ORCHESTRATOR RECOVERY DIAGNOSTIC ===")
    print(f"Timestamp: {timestamp}")
    print(f"Service: gate_orchestrator")
    print()

    try:
        result = query_service_health()
    except SystemExit:
        return False

    if result is None:
        print("Status: NOT REGISTERED")
        print("Last Heartbeat: N/A")
        print()
        print("Error Message:")
        print("gate_orchestrator not found in service_health")
        print()
        print("Classification: N/A")
        print()
        print("Possible Causes:")
        print("- Service never started")
        print("- Service not configured to report health")
        print("- Health record expired or purged")
        print()
        print("Suggested Remediation:")
        print("- Check if gate_orchestrator process is running")
        print("- Verify service is configured to report health")
        print("- Review service startup logs")
        print()
        print("=== END DIAGNOSTIC ===")
        return False

    status = result.get("status", "UNKNOWN")
    last_heartbeat = result.get("last_heartbeat", "N/A")
    meta_str = result.get("meta", "")

    print(f"Status: {status}")
    print(f"Last Heartbeat: {last_heartbeat}")
    print()

    # Extract error message from meta
    error_message = "No error details available"
    if meta_str:
        try:
            meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
            if isinstance(meta, dict):
                error_message = meta.get("error", meta.get("error_message", "No error details available"))
            elif isinstance(meta, str):
                error_message = meta
        except (json.JSONDecodeError, TypeError):
            error_message = meta_str if meta_str else "No error details available"

    print("Error Message:")
    print(error_message)
    print()

    # Classify the error
    classification, matched_keywords = classify_error(error_message)
    print(f"Classification: {classification}")
    if matched_keywords:
        print(f"  Matched keywords: {', '.join(matched_keywords)}")
    print()

    # Get and display possible causes
    print("Possible Causes:")
    for cause in get_possible_causes(classification):
        print(f"- {cause}")
    print()

    # Get and display suggested remediation
    print("Suggested Remediation:")
    for step in get_suggested_remediation(classification):
        print(f"- {step}")
    print()

    print("=== END DIAGNOSTIC ===")
    return True


def main():
    """Entry point when script is run directly."""
    try:
        run()
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    import unittest
    from io import StringIO
    from unittest.mock import patch, MagicMock

    class TestGateOrchestratorDiagnostic(unittest.TestCase):
        """Test cases for gate_orchestrator_recovery_diagnostic.py"""

        def setUp(self):
            """Set up test fixtures."""
            self.timestamp_patcher = patch(
                'gate_orchestrator_recovery_diagnostic.datetime'
            )
            self.mock_datetime = self.timestamp_patcher.start()
            self.mock_datetime.utcnow.return_value = datetime(2024, 1, 15, 10, 30, 0)

        def tearDown(self):
            """Clean up test fixtures."""
            self.timestamp_patcher.stop()

        def test_normal_case_service_found_with_error(self):
            """Test normal case: service found with error classification."""
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [{
                    "service_name": "gate_orchestrator",
                    "status": "error",
                    "last_heartbeat": "2024-01-15T10:29:00Z",
                    "meta": json.dumps({
                        "error": "Failed to load routing config: file not found"
                    })
                }]
            }

            with patch('requests.post', return_value=mock_response):
                with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                    result = run()
                    output = mock_stdout.getvalue()

            self.assertTrue(result)
            self.assertIn("Status: error", output)
            self.assertIn("Classification: INIT", output)
            self.assertIn("Failed to load routing config", output)
            self.assertIn("Possible Causes:", output)
            self.assertIn("Suggested Remediation:", output)
            self.assertIn("=== END DIAGNOSTIC ===", output)

        def test_service_not_found(self):
            """Test case when service is not registered."""
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": []}

            with patch('requests.post', return_value=mock_response):
                with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                    result = run()
                    output = mock_stdout.getvalue()

            self.assertFalse(result)
            self.assertIn("Status: NOT REGISTERED", output)
            self.assertIn("gate_orchestrator not found in service_health", output)
            self.assertIn("Classification: N/A", output)

        def test_connection_error(self):
            """Test case when write_service is unavailable."""
            with patch('requests.post') as mock_post:
                mock_post.side_effect = requests.exceptions.ConnectionError(
                    "Connection refused"
                )
                with patch('sys.stdout', new_callable=StringIO):
                    with self.assertRaises(SystemExit) as cm:
                        run()
                    self.assertEqual(cm.exception.code, 1)

        def test_routing_classification(self):
            """Test routing error classification."""
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [{
                    "service_name": "gate_orchestrator",
                    "status": "error",
                    "last_heartbeat": "2024-01-15T10:29:00Z",
                    "meta": json.dumps({
                        "error": "Signal dispatch failed: routing error"
                    })
                }]
            }

            with patch('requests.post', return_value=mock_response):
                with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                    result = run()
                    output = mock_stdout.getvalue()

            self.assertTrue(result)
            self.assertIn("Classification: ROUTING", output)

        def test_external_classification(self):
            """Test external error classification."""
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [{
                    "service_name": "gate_orchestrator",
                    "status": "error",
                    "last_heartbeat": "2024-01-15T10:29:00Z",
                    "meta": json.dumps({
                        "error": "Database connection timeout after 30s"
                    })
                }]
            }

            with patch('requests.post', return_value=mock_response):
                with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                    result = run()
                    output = mock_stdout.getvalue()

            self.assertTrue(result)
            self.assertIn("Classification: EXTERNAL", output)

        def test_unknown_classification(self):
            """Test unknown error classification."""
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [{
                    "service_name": "gate_orchestrator",
                    "status": "error",
                    "last_heartbeat": "2024-01-15T10:29:00Z",
                    "meta": json.dumps({
                        "error": "Something unexpected happened here"
                    })
                }]
            }

            with patch('requests.post', return_value=mock_response):
                with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                    result = run()
                    output = mock_stdout.getvalue()

            self.assertTrue(result)
            self.assertIn("Classification: UNKNOWN", output)

        def test_http_error_response(self):
            """Test handling of HTTP error responses."""
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"

            with patch('requests.post', return_value=mock_response):
                with patch('sys.stdout', new_callable=StringIO):
                    with self.assertRaises(SystemExit) as cm:
                        run()
                    self.assertEqual(cm.exception.code, 1)

        def test_retry_with_exponential_backoff(self):
            """Test that retries use exponential backoff."""
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [{
                    "service_name": "gate_orchestrator",
                    "status": "healthy",
                    "last_heartbeat": "2024-01-15T10:29:00Z",
                    "meta": "{}"
                }]
            }

            with patch('requests.post', return_value=mock_response) as mock_post:
                with patch('time.sleep') as mock_sleep:
                    run()
                    # Should only be called once on success
                    self.assertEqual(mock_post.call_count, 1)
                    mock_sleep.assert_not_called()

    # Run tests
    unittest.main(verbosity=2)