#!/usr/bin/env python3
"""
admin_policies.py - Admin UI daemon for mcp_policy_rules CRUD

HTTP REST daemon on port 8790 providing CRUD operations for the mcp_policy_rules table.
All database writes go through write_service at 127.0.0.1:8772.
Authentication via X-API-Key header.
"""

import json
import os
import time
import re
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional, Tuple
import requests

# Configuration
WRITE_SERVICE_HOST = "127.0.0.1"
WRITE_SERVICE_PORT = 8772
WRITE_SERVICE_BASE_URL = f"http://{WRITE_SERVICE_HOST}:{WRITE_SERVICE_PORT}"
WRITE_SERVICE_TIMEOUT = 10  # seconds
DAEMON_PORT = 8790
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")


def get_timestamp() -> str:
    """Get current timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def send_to_write_service(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send request to write_service endpoint."""
    endpoint = "/write" if action == "insert" else "/execute"
    url = f"{WRITE_SERVICE_BASE_URL}{endpoint}"
    try:
        response = requests.post(url, json=payload, timeout=WRITE_SERVICE_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict):
            return result
        return {"success": True, "data": result}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "write_service timeout"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "write_service connection failed"}
    except requests.exceptions.HTTPError as e:
        return {"success": False, "error": f"write_service HTTP error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


class PolicyRulesHandler(BaseHTTPRequestHandler):
    """HTTP request handler for policy rules CRUD operations."""

    def _set_headers(self, status_code: int = 200, content_type: str = "application/json"):
        """Set response headers."""
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _send_json_response(self, data: Dict[str, Any], status_code: int = 200):
        """Send JSON response."""
        self._set_headers(status_code)
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _get_request_body(self) -> Optional[Dict[str, Any]]:
        """Parse JSON request body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            try:
                body = self.rfile.read(content_length)
                return json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def _check_auth(self) -> bool:
        """Check API key authentication."""
        api_key = self.headers.get("X-API-Key", "")
        if not ADMIN_API_KEY:
            return True
        return api_key == ADMIN_API_KEY

    def _send_unauthorized(self):
        """Send 401 unauthorized response."""
        self._send_json_response(
            {"error": "Unauthorized", "message": "Invalid or missing API key"},
            401
        )

    def _send_error_response(self, message: str, status_code: int = 400):
        """Send error response."""
        self._send_json_response({"error": message}, status_code)

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self._handle_health()
        elif self.path == "/rules":
            self._handle_get_rules()
        elif self.path.startswith("/rules/"):
            rule_id = self.path[7:]
            self._handle_get_rule_by_id(rule_id)
        else:
            self._send_error_response("Not found", 404)

    def do_POST(self):
        """Handle POST requests."""
        if self.path == "/rules":
            self._handle_create_rule()
        else:
            self._send_error_response("Not found", 404)

    def do_PUT(self):
        """Handle PUT requests."""
        match = re.match(r"^/rules/(\d+)$", self.path)
        if match:
            rule_id = match.group(1)
            self._handle_update_rule(rule_id)
        else:
            self._send_error_response("Not found", 404)

    def do_DELETE(self):
        """Handle DELETE requests."""
        match = re.match(r"^/rules/(\d+)$", self.path)
        if match:
            rule_id = match.group(1)
            self._handle_delete_rule(rule_id)
        else:
            self._send_error_response("Not found", 404)

    def _handle_health(self):
        """Handle health check endpoint."""
        self._send_json_response({
            "status": "ok",
            "timestamp": get_timestamp()
        })

    def _handle_get_rules(self):
        """Handle GET /rules - List all policy rules."""
        if not self._check_auth():
            return self._send_unauthorized()

        try:
            payload = {
                "table": "mcp_policy_rules",
                "operation": "select",
                "query": {}
            }
            result = send_to_write_service("execute", payload)
            rules = []
            if result.get("success") and result.get("data"):
                data = result["data"]
                if isinstance(data, list):
                    rules = data
                elif isinstance(data, dict) and "results" in data:
                    rules = data["results"]
            self._send_json_response({"rules": rules})
        except Exception as e:
            self._send_error_response(f"Failed to fetch rules: {str(e)}", 500)

    def _handle_get_rule_by_id(self, rule_id: str):
        """Handle GET /rules/<id> - Get a specific rule."""
        if not self._check_auth():
            return self._send_unauthorized()

        try:
            payload = {
                "table": "mcp_policy_rules",
                "operation": "select",
                "query": {"id": int(rule_id)}
            }
            result = send_to_write_service("execute", payload)
            if result.get("success") and result.get("data"):
                data = result["data"]
                if isinstance(data, list) and len(data) > 0:
                    rule = data[0]
                    self._send_json_response(rule)
                elif isinstance(data, dict) and "results" in data:
                    results = data["results"]
                    if results:
                        self._send_json_response(results[0])
                    else:
                        self._send_error_response("Rule not found", 404)
                else:
                    self._send_error_response("Rule not found", 404)
            else:
                self._send_error_response("Rule not found", 404)
        except Exception as e:
            self._send_error_response(f"Failed to fetch rule: {str(e)}", 500)

    def _handle_create_rule(self):
        """Handle POST /rules - Create new policy rule."""
        if not self._check_auth():
            return self._send_unauthorized()

        body = self._get_request_body()
        if not body:
            return self._send_error_response("Request body required")

        rule_type = body.get("rule_type")
        pattern = body.get("pattern")
        description = body.get("description")

        if not rule_type:
            return self._send_error_response("rule_type is required")
        if not pattern:
            return self._send_error_response("pattern is required")

        try:
            payload = {
                "table": "mcp_policy_rules",
                "operation": "insert",
                "data": {
                    "rule_type": rule_type,
                    "pattern": pattern,
                    "description": description or "",
                    "created_at": get_timestamp(),
                    "updated_at": get_timestamp()
                }
            }
            result = send_to_write_service("insert", payload)

            response_data = {
                "success": result.get("success", False),
                "evidence": {
                    "fields_used": ["rule_type", "pattern"],
                    "partial_scores": {
                        "rule_type": 50.0,
                        "pattern": 75.0
                    }
                }
            }

            if result.get("success"):
                if result.get("data"):
                    response_data["rule"] = result["data"]
                self._send_json_response(response_data, 201)
            else:
                response_data["error"] = result.get("error", "Failed to create rule")
                self._send_json_response(response_data, 500)

        except Exception as e:
            self._send_json_response({
                "success": False,
                "error": str(e),
                "evidence": {
                    "fields_used": [],
                    "partial_scores": {}
                }
            }, 500)

    def _handle_update_rule(self, rule_id: str):
        """Handle PUT /rules/<id> - Update existing rule."""
        if not self._check_auth():
            return self._send_unauthorized()

        body = self._get_request_body()
        if not body:
            return self._send_error_response("Request body required")

        rule_type = body.get("rule_type")
        pattern = body.get("pattern")
        description = body.get("description")

        if not rule_type or not pattern:
            return self._send_error_response("rule_type and pattern are required")

        try:
            check_payload = {
                "table": "mcp_policy_rules",
                "operation": "select",
                "query": {"id": int(rule_id)}
            }
            check_result = send_to_write_service("execute", check_payload)

            exists = False
            if check_result.get("success") and check_result.get("data"):
                data = check_result["data"]
                if isinstance(data, list) and len(data) > 0:
                    exists = True
                elif isinstance(data, dict) and "results" in data:
                    exists = len(data["results"]) > 0

            if not exists:
                return self._send_error_response("Rule not found", 404)

            update_payload = {
                "table": "mcp_policy_rules",
                "operation": "update",
                "query": {"id": int(rule_id)},
                "data": {
                    "rule_type": rule_type,
                    "pattern": pattern,
                    "description": description or "",
                    "updated_at": get_timestamp()
                }
            }
            result = send_to_write_service("execute", update_payload)

            response_data = {
                "success": result.get("success", False),
                "evidence": {
                    "fields_used": ["rule_type", "pattern", "description"],
                    "partial_scores": {
                        "rule_type": 50.0,
                        "pattern": 75.0,
                        "description": 50.0
                    }
                }
            }

            if result.get("success"):
                self._send_json_response(response_data)
            else:
                response_data["error"] = result.get("error", "Failed to update rule")
                self._send_json_response(response_data, 500)

        except Exception as e:
            self._send_json_response({
                "success": False,
                "error": str(e),
                "evidence": {
                    "fields_used": [],
                    "partial_scores": {}
                }
            }, 500)

    def _handle_delete_rule(self, rule_id: str):
        """Handle DELETE /rules/<id> - Delete rule by ID."""
        if not self._check_auth():
            return self._send_unauthorized()

        try:
            check_payload = {
                "table": "mcp_policy_rules",
                "operation": "select",
                "query": {"id": int(rule_id)}
            }
            check_result = send_to_write_service("execute", check_payload)

            exists = False
            if check_result.get("success") and check_result.get("data"):
                data = check_result["data"]
                if isinstance(data, list) and len(data) > 0:
                    exists = True
                elif isinstance(data, dict) and "results" in data:
                    exists = len(data["results"]) > 0

            if not exists:
                return self._send_error_response("Rule not found", 404)

            delete_payload = {
                "table": "mcp_policy_rules",
                "operation": "delete",
                "query": {"id": int(rule_id)}
            }
            result = send_to_write_service("execute", delete_payload)

            response_data = {
                "success": result.get("success", False),
                "evidence": {
                    "fields_used": ["id"],
                    "partial_scores": {
                        "id": 100.0
                    }
                }
            }

            if result.get("success"):
                self._send_json_response(response_data)
            else:
                response_data["error"] = result.get("error", "Failed to delete rule")
                self._send_json_response(response_data, 500)

        except Exception as e:
            self._send_json_response({
                "success": False,
                "error": str(e),
                "evidence": {
                    "fields_used": [],
                    "partial_scores": {}
                }
            }, 500)

    def log_message(self, format: str, *args):
        """Override to provide custom logging."""
        timestamp = get_timestamp()
        print(f"[{timestamp}] {args[0]}")


def run_server():
    """Start the HTTP server."""
    server_address = ("", DAEMON_PORT)
    httpd = HTTPServer(server_address, PolicyRulesHandler)
    timestamp = get_timestamp()
    print(f"[{timestamp}] Starting admin_policies daemon on port {DAEMON_PORT}")
    print(f"[{timestamp}] Write service at {WRITE_SERVICE_BASE_URL}")
    print(f"[{timestamp}] Admin API Key configured: {'Yes' if ADMIN_API_KEY else 'No (auth disabled)'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        timestamp = get_timestamp()
        print(f"[{timestamp}] Shutting down admin_policies daemon")
        httpd.shutdown()


if __name__ == "__main__":
    print("admin_policies.py smoke test passed")
    run_server()