#!/usr/bin/env python3
"""
ServiceNow inbound webhook handler for MCP request tickets.
Receives webhook requests from ServiceNow, validates signature, and writes to mcp_submissions.
"""

import os
import sys
import hmac
import hashlib
import json
import time
import logging
import threading
import signal
from typing import Optional, Dict, Any, List
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Use standard library only - no external dependencies beyond requests
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
PORT = 8796
HEARTBEAT_INTERVAL = 60
WEBHOOK_PATH = "/webhook/snow"
HEALTH_PATH = "/health"

# Global shutdown event for heartbeat thread
_heartbeat_shutdown = threading.Event()


def get_webhook_secret() -> str:
    """Get webhook secret from environment (fail fast if absent)."""
    secret = os.environ.get('SNOW_WEBHOOK_SECRET')
    if not secret:
        logger.error("SNOW_WEBHOOK_SECRET environment variable not set")
        raise RuntimeError("SNOW_WEBHOOK_SECRET environment variable is required")
    return secret


def compute_signature(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for the given payload."""
    return hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()


def verify_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature using constant-time comparison."""
    if not signature:
        return False
    expected = compute_signature(payload_bytes, secret)
    return hmac.compare_digest(expected, signature)


def get_write_service_url() -> str:
    """Get the write service URL from environment."""
    return os.environ.get('WRITE_SERVICE_URL', 'http://localhost:8797')


def get_health_service_url() -> str:
    """Get the health service URL from environment."""
    return os.environ.get('HEALTH_SERVICE_URL', 'http://localhost:8798')


def write_to_table(table: str, rows: List[Dict], wait: bool = True) -> Optional[Dict]:
    """Write rows to a table via write_service. MUST NOT write to other tables."""
    allowed_tables = {'mcp_submissions', 'audit_log'}
    if table not in allowed_tables:
        raise ValueError(f"Cannot write to table '{table}' - not in allowed list: {allowed_tables}")
    
    write_service_url = get_write_service_url()
    url = f"{write_service_url}/write"
    
    payload = {
        'table': table,
        'rows': rows,
        'wait': wait
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to write to {table}: {e}")
        raise


def send_heartbeat() -> None:
    """Send heartbeat to service_health."""
    health_url = get_health_service_url()
    payload = {
        'service': 'snow_inbound_webhook',
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    }
    
    try:
        response = requests.post(
            f"{health_url}/heartbeat",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        logger.debug("Heartbeat sent successfully")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Heartbeat failed: {e}")


def heartbeat_loop() -> None:
    """Background thread for sending heartbeats every 60s."""
    logger.info("Starting heartbeat loop (interval=%ds)", HEARTBEAT_INTERVAL)
    while not _heartbeat_shutdown.wait(HEARTBEAT_INTERVAL):
        try:
            send_heartbeat()
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
    logger.info("Heartbeat loop stopped")


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for ServiceNow webhook."""
    
    def log_message(self, format, *args):
        """Override to use our logger."""
        logger.info("%s - %s", self.address_string(), format % args)
    
    def do_GET(self):
        """Handle GET requests (health check)."""
        if self.path == HEALTH_PATH:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {'status': 'healthy', 'service': 'snow_inbound_webhook'}
            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        """Handle POST requests (webhook)."""
        if self.path != WEBHOOK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        
        try:
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            
            # Get signature from header
            signature = self.headers.get('X-SNOW-Signature', '')
            
            # Validate signature (constant-time comparison)
            secret = get_webhook_secret()
            if not verify_signature(body, signature, secret):
                logger.warning("Rejected request with invalid/missing signature")
                self.send_response(401)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid or missing signature'}).encode('utf-8'))
                return
            
            # Parse payload
            try:
                payload = json.loads(body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("Invalid JSON payload received")
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON payload'}).encode('utf-8'))
                return
            
            # Extract fields from ServiceNow payload
            short_description = payload.get('short_description', '')
            description = payload.get('description', '')
            caller_id = payload.get('caller_id', '')
            server_name = payload.get('u_mcp_server_name', '')
            priority = payload.get('u_priority', '3')
            
            # Construct mcp_submissions row
            row = {
                'mcp_name': server_name,
                'requested_by': caller_id,
                'status': 'pending_review',
                'priority': priority,
                'notes': short_description
            }
            
            # Write to mcp_submissions (MUST NOT write to other tables)
            result = write_to_table('mcp_submissions', [row], wait=True)
            submission_id = result.get('server_id', 'unknown') if result else 'unknown'
            
            # Log to audit_log (MUST NOT write to other tables)
            audit_entry = {
                'action': 'snow_webhook_received',
                'target_server_name': server_name,
                'timestamp': datetime.utcnow().isoformat(),
                'caller_id': caller_id
            }
            write_to_table('audit_log', [audit_entry])
            
            # Return success response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {'status': 'accepted', 'submission_id': submission_id}
            self.wfile.write(json.dumps(response).encode('utf-8'))
            logger.info("Webhook processed: server=%s, caller=%s, priority=%s", 
                       server_name, caller_id, priority)
            
        except Exception as e:
            logger.error("Webhook processing error: %s", e)
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Internal server error'}).encode('utf-8'))


def run() -> None:
    """Run the webhook server with daemon pattern."""
    # Fail fast if secret not set
    secret = get_webhook_secret()
    logger.info("SNOW_WEBHOOK_SECRET validated")
    
    # Start heartbeat thread
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    logger.info("Heartbeat thread started (service=snow_inbound_webhook, interval=%ds)", HEARTBEAT_INTERVAL)
    
    # Start HTTP server
    server = HTTPServer(('0.0.0.0', PORT), WebhookHandler)
    logger.info("Server listening on port %d", PORT)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _heartbeat_shutdown.set()
        server.shutdown()
        logger.info("Server shutdown complete")


def run_self_test() -> None:
    """Self-test: verify server starts and signature validation works."""
    import socket
    
    logger.info("=" * 60)
    logger.info("Running self-test...")
    logger.info("=" * 60)
    
    # Set up isolated test environment
    test_secret = "test_secret_12345"
    os.environ["SNOW_WEBHOOK_SECRET"] = test_secret
    os.environ["WRITE_SERVICE_URL"] = "http://localhost:18797"
    os.environ["HEALTH_SERVICE_URL"] = "http://localhost:18798"
    
    # Track shutdown
    shutdown_event = threading.Event()
    
    def run_server():
        _heartbeat_shutdown.clear()
        run()
    
    # Start server in background thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # Wait for server startup (max 10s as per constraint)
    base_url = f"http://127.0.0.1:{PORT}"
    server_ready = False
    for i in range(20):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', PORT))
            sock.close()
            if result == 0:
                server_ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    
    assert server_ready, f"Server did not start on port {PORT} within 10s"
    logger.info("✓ Server is listening on port %d", PORT)
    
    try:
        # Test 1: Missing signature -> 401
        logger.info("Test 1: Request without X-SNOW-Signature header")
        resp = requests.post(f"{base_url}{WEBHOOK_PATH}", json={"test": 1}, timeout=5)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        logger.info("✓ Missing signature rejected with 401")
        
        # Test 2: Invalid signature -> 401
        logger.info("Test 2: Request with invalid X-SNOW-Signature header")
        headers = {"X-SNOW-Signature": "invalid_signature_0000"}
        resp = requests.post(f"{base_url}{WEBHOOK_PATH}", json={"test": 1}, headers=headers, timeout=5)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        logger.info("✓ Invalid signature rejected with 401")
        
        # Test 3: Valid HMAC signature -> 200
        logger.info("Test 3: Request with valid HMAC-SHA256 signature")
        test_payload = {"short_description": "Test request", "description": "Test", 
                       "caller_id": "test.user", "u_mcp_server_name": "test-server", 
                       "u_priority": "2"}
        body = json.dumps(test_payload).encode('utf-8')
        valid_sig = compute_signature(body, test_secret)
        headers = {"X-SNOW-Signature": valid_sig}
        resp = requests.post(f"{base_url}{WEBHOOK_PATH}", data=body, headers=headers, timeout=5)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        resp_data = resp.json()
        assert resp_data.get('status') == 'accepted', f"Expected status=accepted, got {resp_data}"
        logger.info("✓ Valid signature accepted with 200")
        logger.info("  Response: %s", resp_data)
        
        logger.info("=" * 60)
        logger.info("All self-tests PASSED!")
        logger.info("=" * 60)
        
    finally:
        # Trigger shutdown
        _heartbeat_shutdown.set()
        # Give threads time to clean up
        time.sleep(0.5)
    
    sys.exit(0)


if __name__ == "__main__":
    run()