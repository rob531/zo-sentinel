#!/usr/bin/env python3
"""
ServiceNow inbound webhook handler for MCP request tickets.

Receives requests from ServiceNow, validates signature, and writes to mcp_submissions
table for analyst triage.

Binds to port 8796. Daemon pattern with run() function.
"""

import os
import sys
import hmac
import hashlib
import json
import logging
import time
import threading
from typing import Any, Dict, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('snow_inbound_webhook')

# Constants
DEFAULT_PORT = 8796
HEARTBEAT_INTERVAL = 60  # seconds
HEALTH_SERVICE_NAME = 'snow_inbound_webhook'

# Environment variable for webhook secret
SNOW_WEBHOOK_SECRET = os.environ.get('SNOW_WEBHOOK_SECRET')

# Write service configuration (passed via environment or defaults)
WRITE_SERVICE_URL = os.environ.get('WRITE_SERVICE_URL', 'http://localhost:8797/write')


class RequestHandler(BaseHTTPRequestHandler):
    """FastAPI-style request handler for webhook endpoints."""

    def log_message(self, format: str, *args) -> None:
        """Override to use our logger."""
        logger.info(f"{self.address_string()} - {format % args}")

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.path == '/webhook/snow':
            self._handle_snow_webhook()
        elif self.path == '/health':
            self._handle_health()
        elif self.path == '/ready':
            self._handle_ready()
        else:
            self._send_response(404, {'error': 'Not found'})

    def _handle_health(self) -> None:
        """Health check endpoint."""
        self._send_response(200, {'status': 'healthy'})

    def _handle_ready(self) -> None:
        """Readiness check endpoint."""
        if SNOW_WEBHOOK_SECRET is None:
            self._send_response(503, {'status': 'not ready', 'reason': 'SNOW_WEBHOOK_SECRET not set'})
        else:
            self._send_response(200, {'status': 'ready'})

    def _handle_snow_webhook(self) -> None:
        """Handle ServiceNow webhook POST requests."""
        # Check for required secret
        if SNOW_WEBHOOK_SECRET is None:
            logger.error("SNOW_WEBHOOK_SECRET environment variable not set")
            self._send_response(500, {'error': 'Server misconfiguration'})
            return

        # Get content length
        content_length = self.headers.get('Content-Length')
        if content_length is None:
            logger.warning("Missing Content-Length header")
            self._send_response(400, {'error': 'Missing Content-Length header'})
            return

        try:
            content_length = int(content_length)
        except ValueError:
            logger.warning("Invalid Content-Length header")
            self._send_response(400, {'error': 'Invalid Content-Length header'})
            return

        # Read request body
        try:
            body = self.rfile.read(content_length)
        except Exception as e:
            logger.warning(f"Failed to read request body: {e}")
            self._send_response(400, {'error': 'Failed to read request body'})
            return

        # Get signature header
        signature_header = self.headers.get('X-SNOW-Signature')
        if signature_header is None:
            logger.warning("Missing X-SNOW-Signature header")
            self._send_response(401, {'error': 'Missing X-SNOW-Signature header'})
            return

        # Validate signature using constant-time comparison
        expected_signature = hmac.new(
            SNOW_WEBHOOK_SECRET.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature_header.lower(), expected_signature.lower()):
            logger.warning("Invalid signature - signature mismatch")
            self._send_response(401, {'error': 'Invalid signature'})
            return

        # Parse payload
        try:
            payload = json.loads(body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to parse JSON payload: {e}")
            self._send_response(400, {'error': 'Invalid JSON payload'})
            return

        # Extract fields from payload
        # ServiceNow field names: u_mcp_server_name, u_priority, etc.
        mcp_server_name = payload.get('u_mcp_server_name', payload.get('mcp_server_name'))
        priority = payload.get('u_priority', payload.get('priority', '3'))
        short_description = payload.get('short_description', '')
        description = payload.get('description', '')
        caller_id = payload.get('caller_id', payload.get('u_caller_id', 'unknown'))

        # Build notes from description and short_description
        notes = short_description
        if description and description != short_description:
            notes = f"{short_description}\n\n{description}" if short_description else description

        # Log extracted fields (without credentials)
        logger.info(f"Processing webhook - server: {mcp_server_name}, priority: {priority}, caller: {caller_id}")

        # Write to mcp_submissions table
        submission_row = {
            'mcp_name': mcp_server_name,
            'requested_by': caller_id,
            'status': 'pending_review',
            'priority': str(priority),
            'notes': notes
        }

        try:
            write_result = _write_to_service(
                table='mcp_submissions',
                rows=[submission_row],
                wait=True
            )
        except Exception as e:
            logger.error(f"Failed to write to mcp_submissions: {e}")
            self._send_response(500, {'error': 'Failed to record submission'})
            return

        # Log to audit_log
        try:
            _write_to_service(
                table='audit_log',
                rows=[{
                    'action': 'snow_webhook_received',
                    'target_server_name': mcp_server_name,
                    'caller_id': caller_id,
                    'priority': str(priority)
                }],
                wait=False  # Don't wait for audit log
            )
        except Exception as e:
            # Log error but don't fail the request - audit logging is secondary
            logger.warning(f"Failed to write to audit_log: {e}")

        # Return success response
        submission_id = write_result.get('server_id') or write_result.get('id') or write_result.get('note') or 'recorded'
        logger.info(f"Submission accepted - id: {submission_id}")
        self._send_response(200, {
            'status': 'accepted',
            'submission_id': submission_id
        })

    def _send_response(self, status_code: int, body: Dict[str, Any]) -> None:
        """Send JSON response."""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode('utf-8'))


def _write_to_service(table: str, rows: list, wait: bool = False) -> Dict[str, Any]:
    """
    Write rows to specified table via write service.
    
    Args:
        table: Table name to write to (must be mcp_submissions or audit_log)
        rows: List of row dictionaries to write
        wait: Whether to wait for completion
        
    Returns:
        Response from write service
        
    Raises:
        ValueError: If table name is not allowed
    """
    # MUST NOT write to any table other than mcp_submissions and audit_log
    allowed_tables = {'mcp_submissions', 'audit_log'}
    if table not in allowed_tables:
        raise ValueError(f"Table '{table}' is not allowed. Must be one of: {allowed_tables}")

    import requests

    payload = {
        'table': table,
        'rows': rows,
        'wait': wait
    }

    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json=payload,
            timeout=30 if wait else 5
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Write service request failed: {e}")
        raise


def _send_heartbeat() -> None:
    """Send heartbeat to service_health."""
    import requests

    health_url = os.environ.get('HEALTH_SERVICE_URL', 'http://localhost:8798/health')
    
    payload = {
        'service': HEALTH_SERVICE_NAME,
        'status': 'running',
        'timestamp': time.time()
    }

    try:
        response = requests.post(
            health_url,
            json=payload,
            timeout=5
        )
        if response.ok:
            logger.debug("Heartbeat sent successfully")
        else:
            logger.warning(f"Heartbeat failed with status {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Heartbeat failed: {e}")


class HeartbeatThread(threading.Thread):
    """Background thread for sending periodic heartbeats."""

    def __init__(self):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()

    def run(self) -> None:
        """Send heartbeats until stopped."""
        while not self._stop_event.is_set():
            try:
                _send_heartbeat()
            except Exception as e:
                logger.warning(f"Heartbeat error: {e}")
            
            # Wait for interval or stop event
            self._stop_event.wait(HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        """Stop the heartbeat thread."""
        self._stop_event.set()


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded HTTP server for handling concurrent requests."""
    allow_reuse_address = True


def run(host: str = '0.0.0.0', port: int = DEFAULT_PORT) -> None:
    """
    Run the webhook server.
    
    Args:
        host: Host to bind to
        port: Port to bind to
    """
    # Check for required environment variable
    if SNOW_WEBHOOK_SECRET is None:
        logger.error("SNOW_WEBHOOK_SECRET environment variable is required but not set")
        raise ValueError("SNOW_WEBHOOK_SECRET environment variable is required")

    logger.info(f"Starting ServiceNow webhook handler on {host}:{port}")

    # Start heartbeat thread
    heartbeat_thread = HeartbeatThread()
    heartbeat_thread.start()

    # Start server
    server = ThreadedHTTPServer((host, port), RequestHandler)
    
    logger.info(f"ServiceNow webhook handler listening on {host}:{port}")
    logger.info(f"Endpoints: POST /webhook/snow, GET /health, GET /ready")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        heartbeat_thread.stop()
        server.shutdown()
        server.server_close()
        logger.info("Server stopped")


def self_test() -> None:
    """
    Self-test for the webhook handler.
    
    Tests:
    1. Server starts and listens on port 8796
    2. Wrong signature returns 401
    3. Correct HMAC signature returns 200
    """
    import requests
    import threading
    import time

    test_results = []
    
    def assert_test(condition: bool, message: str) -> None:
        if condition:
            logger.info(f"PASS: {message}")
            test_results.append(True)
        else:
            logger.error(f"FAIL: {message}")
            test_results.append(False)

    # Set up test secret
    test_secret = 'test_webhook_secret_12345'
    os.environ['SNOW_WEBHOOK_SECRET'] = test_secret

    # Start server in background thread
    server_thread = threading.Thread(target=run, daemon=True)
    server_started = threading.Event()
    
    def start_and_notify():
        # Patch the port for testing
        global DEFAULT_PORT
        DEFAULT_PORT = 8796
        run(port=8796)
    
    server_thread = threading.Thread(target=start_and_notify, daemon=True)
    server_thread.start()
    
    # Wait for server to start (up to 5 seconds)
    base_url = 'http://localhost:8796'
    server_ready = False
    for _ in range(50):
        try:
            response = requests.get(f'{base_url}/ready', timeout=1)
            if response.status_code == 200:
                server_ready = True
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.1)

    assert_test(server_ready, "Server started and ready on port 8796")

    # Test 1: Wrong signature should return 401
    try:
        response = requests.post(
            f'{base_url}/webhook/snow',
            json={
                'short_description': 'Test request',
                'description': 'Test description',
                'caller_id': 'test.user',
                'u_mcp_server_name': 'test-server',
                'u_priority': '2'
            },
            headers={'X-SNOW-Signature': 'invalid_signature'},
            timeout=5
        )
        assert_test(response.status_code == 401, "Wrong signature returns 401")
    except Exception as e:
        logger.error(f"Test 1 error: {e}")
        test_results.append(False)

    # Test 2: Missing signature should return 401
    try:
        response = requests.post(
            f'{base_url}/webhook/snow',
            json={
                'short_description': 'Test request',
                'caller_id': 'test.user',
                'u_mcp_server_name': 'test-server',
                'u_priority': '2'
            },
            timeout=5
        )
        assert_test(response.status_code == 401, "Missing signature returns 401")
    except Exception as e:
        logger.error(f"Test 2 error: {e}")
        test_results.append(False)

    # Test 3: Correct HMAC signature should return 200
    try:
        payload = {
            'short_description': 'Test request',
            'description': 'Test description',
            'caller_id': 'test.user',
            'u_mcp_server_name': 'test-server',
            'u_priority': '2'
        }
        body = json.dumps(payload)
        correct_signature = hmac.new(
            test_secret.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        response = requests.post(
            f'{base_url}/webhook/snow',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'X-SNOW-Signature': correct_signature
            },
            timeout=5
        )
        assert_test(response.status_code == 200, "Correct signature returns 200")
        
        if response.status_code == 200:
            response_data = response.json()
            assert_test(response_data.get('status') == 'accepted', "Response contains 'accepted' status")
    except Exception as e:
        logger.error(f"Test 3 error: {e}")
        test_results.append(False)

    # Test 4: Health endpoint
    try:
        response = requests.get(f'{base_url}/health', timeout=5)
        assert_test(response.status_code == 200, "Health endpoint returns 200")
    except Exception as e:
        logger.error(f"Test 4 error: {e}")
        test_results.append(False)

    # Give server time to clean up, then force exit
    time.sleep(0.5)
    
    # Print summary
    passed = sum(test_results)
    total = len(test_results)
    logger.info(f"\n{'='*50}")
    logger.info(f"Self-test results: {passed}/{total} tests passed")
    
    if all(test_results):
        logger.info("All tests PASSED")
        sys.exit(0)
    else:
        logger.error("Some tests FAILED")
        sys.exit(1)


if __name__ == '__main__':
    # Check if running in self-test mode
    if '--self-test' in sys.argv or os.environ.get('RUN_SELF_TEST') == '1':
        self_test()
    else:
        run()