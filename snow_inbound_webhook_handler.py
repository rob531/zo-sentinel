#!/usr/bin/env python3
"""
ServiceNow inbound webhook handler.
Receives MCP request tickets from ServiceNow, validates signature, writes to mcp_submissions.
"""

import os
import sys
import hmac
import hashlib
import logging
import threading
import time
import json
import secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment
SNOW_WEBHOOK_SECRET = os.environ.get('SNOW_WEBHOOK_SECRET')
if not SNOW_WEBHOOK_SECRET:
    raise RuntimeError("SNOW_WEBHOOK_SECRET environment variable is required")

# Service endpoints
WRITE_SERVICE_URL = os.environ.get('WRITE_SERVICE_URL', 'http://localhost:8080')
HEALTH_SERVICE_URL = os.environ.get('HEALTH_SERVICE_URL', WRITE_SERVICE_URL)

# Server config
SERVER_PORT = 8796
HEARTBEAT_INTERVAL = 60

# Thread pool for handling requests
_executor = ThreadPoolExecutor(max_workers=4)

# Shutdown flag
_shutdown_event = threading.Event()


def validate_signature(body: bytes, signature: str) -> bool:
    """Validate HMAC-SHA256 signature using constant-time comparison."""
    if not signature:
        return False
    expected = hmac.new(
        SNOW_WEBHOOK_SECRET.encode('utf-8'),
        body,
        hashlib.sha256
    ).hexdigest()
    # Constant-time comparison to prevent timing attacks
    return secrets.compare_digest(expected, signature.lower())


def write_to_service(endpoint: str, table: str, rows: list, wait: bool = True) -> dict:
    """Write rows to specified table via write_service."""
    payload = {
        'table': table,
        'rows': rows,
        'wait': wait
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}{endpoint}",
        json=payload,
        timeout=10
    )
    response.raise_for_status()
    return response.json()


def log_to_audit(action: str, target_server_name: str = None, **kwargs) -> None:
    """Log action to audit_log table."""
    audit_row = {
        'action': action,
        'target_server_name': target_server_name,
        **kwargs
    }
    # Remove None values
    audit_row = {k: v for k, v in audit_row.items() if v is not None}
    try:
        write_to_service('/write', 'audit_log', [audit_row], wait=True)
    except Exception as e:
        # Log but don't fail the request
        logger.warning("Failed to write audit log: %s", e)


def send_heartbeat() -> None:
    """Send heartbeat to service_health."""
    try:
        response = requests.post(
            f"{HEALTH_SERVICE_URL}/service_health",
            json={'service': 'snow_inbound_webhook'},
            timeout=5
        )
        response.raise_for_status()
        logger.debug("Heartbeat sent successfully")
    except requests.RequestException as e:
        logger.warning("Heartbeat failed: %s", e)


def heartbeat_loop() -> None:
    """Background thread for periodic heartbeats."""
    while not _shutdown_event.is_set():
        time.sleep(HEARTBEAT_INTERVAL)
        if _shutdown_event.is_set():
            break
        try:
            send_heartbeat()
        except Exception as e:
            logger.warning("Heartbeat error: %s", e)


class WebhookHandler(BaseHTTPRequestHandler):
    """Handle incoming webhook requests."""

    def log_message(self, format, *args):
        """Override to use our logger."""
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_json_response(self, status_code: int, data: dict) -> None:
        """Send JSON response."""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.path != '/webhook/snow':
            self._send_json_response(404, {'error': 'Not Found'})
            return

        # Read request body
        content_length = int(self.headers.get('Content-Length', 0) or 0)
        body = self.rfile.read(content_length)

        # Get signature header
        signature = self.headers.get('X-SNOW-Signature', '')

        # MUST NOT rule 1: Validate signature
        if not validate_signature(body, signature):
            logger.warning("Invalid or missing signature from %s", self.address_string())
            self._send_json_response(401, {'error': 'Unauthorized'})
            return

        # Parse payload
        try:
            payload = json.loads(body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Invalid JSON payload: %s", e)
            self._send_json_response(400, {'error': 'Invalid JSON payload'})
            return

        # Extract fields
        mcp_server_name = payload.get('u_mcp_server_name', '')
        priority = payload.get('u_priority', '3')
        short_description = payload.get('short_description', '')
        caller_id = payload.get('caller_id', '')

        # MUST NOT rule 3: Don't log raw body or secret
        logger.info("Processing webhook: server=%s, caller=%s",
                    mcp_server_name, caller_id)

        # Construct mcp_submissions row
        submission_row = {
            'mcp_name': mcp_server_name,
            'requested_by': caller_id,
            'status': 'pending_review',
            'priority': priority,
            'notes': short_description
        }

        # Write to mcp_submissions table
        try:
            result = write_to_service('/write', 'mcp_submissions', [submission_row], wait=True)
            submission_id = result.get('server_id', str(result.get('id', 'unknown')))
            logger.info("Submission created: id=%s", submission_id)
        except Exception as e:
            logger.error("Failed to write submission: %s", e)
            self._send_json_response(500, {'error': 'Failed to create submission'})
            return

        # Log to audit_log (MUST NOT rule 2: only audit_log and mcp_submissions)
        log_to_audit(
            action='snow_webhook_received',
            target_server_name=mcp_server_name,
            caller_id=caller_id,
            priority=priority
        )

        # Return success response
        self._send_json_response(200, {
            'status': 'accepted',
            'submission_id': submission_id
        })

    def do_GET(self) -> None:
        """Handle GET requests (health check)."""
        if self.path == '/health':
            self._send_json_response(200, {'status': 'healthy'})
        else:
            self._send_json_response(404, {'error': 'Not Found'})


def run() -> None:
    """Start the webhook server (daemon pattern)."""
    # Start heartbeat thread
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    server_address = ('', SERVER_PORT)
    httpd = HTTPServer(server_address, WebhookHandler)
    
    logger.info("ServiceNow webhook handler starting on port %d", SERVER_PORT)
    logger.info("Ready to receive MCP request tickets")

    # Serve until shutdown
    while not _shutdown_event.is_set():
        httpd.handle_request()

    httpd.server_close()
    logger.info("Server shut down cleanly")


def shutdown() -> None:
    """Signal server shutdown."""
    _shutdown_event.set()


def self_test() -> int:
    """
    Self-test: Start server, test endpoints, verify behavior.
    Returns exit code (0 = success).
    """
    import socket
    import signal

    # Set test secret for self-test
    os.environ['SNOW_WEBHOOK_SECRET'] = 'test-secret-key-12345'
    os.environ['WRITE_SERVICE_URL'] = 'http://localhost:9999'  # Won't actually connect

    # Import after setting env
    global SNOW_WEBHOOK_SECRET
    SNOW_WEBHOOK_SECRET = os.environ['SNOW_WEBHOOK_SECRET']

    logger.info("=== Starting self-test ===")

    # Start server in background thread
    server_thread = threading.Thread(target=run, daemon=True)
    server_thread.start()

    # Wait for server to start
    time.sleep(0.5)

    base_url = f'http://localhost:{SERVER_PORT}'
    test_passed = True

    def is_port_open() -> bool:
        """Check if server is listening on port."""
        try:
            with socket.create_connection(('localhost', SERVER_PORT), timeout=2):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    # Test 1: Server is listening
    logger.info("Test 1: Checking server is listening on port %d...", SERVER_PORT)
    if is_port_open():
        logger.info("  PASS: Server is listening")
    else:
        logger.error("  FAIL: Server not listening")
        test_passed = False

    # Test 2: Health endpoint
    try:
        resp = requests.get(f'{base_url}/health', timeout=2)
        if resp.status_code == 200:
            logger.info("  PASS: Health endpoint returns 200")
        else:
            logger.error("  FAIL: Health endpoint returned %d", resp.status_code)
            test_passed = False
    except requests.RequestException as e:
        logger.error("  FAIL: Health endpoint error: %s", e)
        test_passed = False

    # Test 3: Wrong signature -> 401
    logger.info("Test 2: Testing wrong signature -> 401...")
    try:
        test_payload = {
            'short_description': 'Test ticket',
            'description': 'Test description',
            'caller_id': 'test.user',
            'u_mcp_server_name': 'test-server',
            'u_priority': '2'
        }
        body = json.dumps(test_payload).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'X-SNOW-Signature': 'invalid_signature'}

        resp = requests.post(f'{base_url}/webhook/snow', data=body, headers=headers, timeout=5)
        if resp.status_code == 401:
            logger.info("  PASS: Wrong signature returns 401")
        else:
            logger.error("  FAIL: Expected 401, got %d", resp.status_code)
            test_passed = False
    except requests.RequestException as e:
        logger.error("  FAIL: Request error: %s", e)
        test_passed = False

    # Test 4: Missing signature -> 401
    logger.info("Test 3: Testing missing signature -> 401...")
    try:
        test_payload = {
            'short_description': 'Test ticket',
            'description': 'Test description',
            'caller_id': 'test.user',
            'u_mcp_server_name': 'test-server',
            'u_priority': '2'
        }
        body = json.dumps(test_payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}

        resp = requests.post(f'{base_url}/webhook/snow', data=body, headers=headers, timeout=5)
        if resp.status_code == 401:
            logger.info("  PASS: Missing signature returns 401")
        else:
            logger.error("  FAIL: Expected 401, got %d", resp.status_code)
            test_passed = False
    except requests.RequestException as e:
        logger.error("  FAIL: Request error: %s", e)
        test_passed = False

    # Test 5: Correct HMAC -> 200
    logger.info("Test 4: Testing correct HMAC -> 200...")
    try:
        test_payload = {
            'short_description': 'Test ticket',
            'description': 'Test description',
            'caller_id': 'test.user',
            'u_mcp_server_name': 'test-server',
            'u_priority': '2'
        }
        body = json.dumps(test_payload).encode('utf-8')
        
        # Generate correct HMAC
        correct_sig = hmac.new(
            SNOW_WEBHOOK_SECRET.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()

        headers = {'Content-Type': 'application/json', 'X-SNOW-Signature': correct_sig}

        resp = requests.post(f'{base_url}/webhook/snow', data=body, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'accepted':
                logger.info("  PASS: Correct HMAC returns 200 with accepted status")
            else:
                logger.error("  FAIL: Unexpected response body: %s", data)
                test_passed = False
        else:
            logger.error("  FAIL: Expected 200, got %d", resp.status_code)
            test_passed = False
    except requests.RequestException as e:
        logger.error("  FAIL: Request error: %s", e)
        test_passed = False

    # Test 6: Invalid JSON -> 400
    logger.info("Test 5: Testing invalid JSON -> 400...")
    try:
        body = b'not valid json'
        correct_sig = hmac.new(
            SNOW_WEBHOOK_SECRET.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()
        headers = {'Content-Type': 'application/json', 'X-SNOW-Signature': correct_sig}

        resp = requests.post(f'{base_url}/webhook/snow', data=body, headers=headers, timeout=5)
        if resp.status_code == 400:
            logger.info("  PASS: Invalid JSON returns 400")
        else:
            logger.error("  FAIL: Expected 400, got %d", resp.status_code)
            test_passed = False
    except requests.RequestException as e:
        logger.error("  FAIL: Request error: %s", e)
        test_passed = False

    # Test 7: Wrong path -> 404
    logger.info("Test 6: Testing wrong path -> 404...")
    try:
        resp = requests.post(f'{base_url}/webhook/wrong', timeout=5)
        if resp.status_code == 404:
            logger.info("  PASS: Wrong path returns 404")
        else:
            logger.error("  FAIL: Expected 404, got %d", resp.status_code)
            test_passed = False
    except requests.RequestException as e:
        logger.error("  FAIL: Request error: %s", e)
        test_passed = False

    # Shutdown server
    shutdown()
    
    # Clean up test env
    if 'SNOW_WEBHOOK_SECRET' in os.environ and os.environ['SNOW_WEBHOOK_SECRET'] == 'test-secret-key-12345':
        del os.environ['SNOW_WEBHOOK_SECRET']

    if test_passed:
        logger.info("=== All tests passed ===")
        return 0
    else:
        logger.error("=== Some tests failed ===")
        return 1


if __name__ == '__main__':
    # Run self-test
    exit_code = self_test()
    sys.exit(exit_code)