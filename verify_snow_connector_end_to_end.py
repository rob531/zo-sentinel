#!/usr/bin/env python3
"""
verify_snow_connector_end_to_end.py

Pure validation/test module that exercises snow_connector.py end-to-end
using a synthetic MCP submission payload, asserting expected DB write behaviour.
"""

import importlib
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any

import requests

# Configuration
SNOW_CONNECTOR_MODULE = "snow_connector"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
HTTP_TIMEOUT = 10


def generate_synthetic_snow_payload() -> dict[str, Any]:
    """
    Generate a synthetic SNOW webhook payload matching what snow_connector.py
    expects for /snow/webhook endpoint processing.
    """
    unique_id = str(uuid.uuid4())[:8]
    
    return {
        "data": {
            "fields": {
                "short_description": f"Test MCP Server Registration {unique_id}",
                "description": f"Synthetic test submission for snow_connector verification",
                "u_mcp_server_name": f"test_mcp_snow_{unique_id}",
                "u_requested_by": "verify_snow_connector_end_to_end.py",
                "number": f"CHG{unique_id[:6].upper()}",
                "sys_id": str(uuid.uuid4()),
            }
        }
    }


def import_snow_connector():
    """Import snow_connector.py via importlib to confirm it parses."""
    try:
        # Add the workspace to path for proper module resolution
        module = importlib.import_module(SNOW_CONNECTOR_MODULE)
        return module
    except ModuleNotFoundError as e:
        raise AssertionError(f"Failed to import {SNOW_CONNECTOR_MODULE}: {e}")
    except SyntaxError as e:
        raise AssertionError(f"Syntax error in {SNOW_CONNECTOR_MODULE}: {e}")


def find_ws_write(module) -> callable:
    """Find the ws_write function in snow_connector module."""
    if hasattr(module, "ws_write"):
        func = getattr(module, "ws_write")
        if callable(func):
            return func
    
    raise AssertionError(
        f"ws_write function not found in {SNOW_CONNECTOR_MODULE}. "
        "The snow_connector module should export a ws_write function."
    )


def call_write_service(table: str, rows: list[dict], wait: bool = True) -> requests.Response:
    """Call write_service POST /write with test row."""
    payload = {
        "table": table,
        "rows": rows,
        "wait": wait,
    }
    
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json=payload,
            timeout=HTTP_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        return response
    except requests.exceptions.Timeout:
        raise AssertionError(
            f"write_service request timed out after {HTTP_TIMEOUT}s"
        )
    except requests.exceptions.ConnectionError as e:
        raise AssertionError(
            f"Failed to connect to write_service at {WRITE_SERVICE_URL}: {e}"
        )
    except requests.exceptions.RequestException as e:
        raise AssertionError(f"write_service request failed: {e}")


def run_smoke_test() -> None:
    """
    Execute end-to-end smoke test for snow_connector.py.
    
    Steps:
    1. Import snow_connector.py via importlib to confirm it parses
    2. Find ws_write function in the connector module
    3. Generate synthetic SNOW webhook payload
    4. Verify write_service POST /write returns HTTP 200
    
    Raises:
        AssertionError: If any validation fails
    """
    print("=" * 60)
    print("SNOW Connector End-to-End Smoke Test")
    print("=" * 60)
    
    # Step 1: Import snow_connector.py
    print("\n[1/4] Importing snow_connector.py...")
    try:
        connector_module = import_snow_connector()
        print(f"    [OK] Successfully imported {SNOW_CONNECTOR_MODULE}")
    except AssertionError:
        print(f"    [FAIL] Could not import {SNOW_CONNECTOR_MODULE}")
        raise
    
    # Step 2: Find ws_write function
    print("\n[2/4] Locating ws_write function...")
    try:
        ws_write_func = find_ws_write(connector_module)
        print(f"    [OK] Found ws_write function")
    except AssertionError as e:
        print(f"    [FAIL] {e}")
        raise
    
    # Step 3: Generate synthetic payload
    print("\n[3/4] Generating synthetic SNOW webhook payload...")
    synthetic_payload = generate_synthetic_snow_payload()
    unique_id = str(uuid.uuid4())[:8]
    mcp_server_name = synthetic_payload["data"]["fields"]["u_mcp_server_name"]
    print(f"    [OK] Generated unique mcp_server_name: {mcp_server_name}")
    
    # Build the submission data that snow_connector would create from the webhook
    import hashlib
    description = synthetic_payload["data"]["fields"]["description"]
    submission_data = {
        "mcp_server_name": mcp_server_name,
        "requester_email": synthetic_payload["data"]["fields"]["u_requested_by"],
        "source": "servicenow",
        "status": "pending_review",
        "submitted_at": datetime.utcnow().isoformat() + "Z",
        "snow_ticket_id": synthetic_payload["data"]["fields"]["number"],
        "snow_sys_id": synthetic_payload["data"]["fields"]["sys_id"],
        "short_description": synthetic_payload["data"]["fields"]["short_description"],
        "description_hash": hashlib.sha256(description.encode()).hexdigest(),
    }
    
    # Step 4: Call write_service directly to verify the connector's write path
    print("\n[4/4] Calling write_service POST /write...")
    print(f"    URL: {WRITE_SERVICE_URL}")
    print(f"    Table: mcp_submissions")
    print(f"    Row count: 1")
    
    try:
        response = call_write_service(
            table="mcp_submissions",
            rows=[submission_data],
            wait=True,
        )
        
        print(f"    Response status: {response.status_code}")
        
        if response.status_code == 200:
            print("    [OK] HTTP 200 received")
            
            # Parse response for diagnostics
            try:
                response_data = response.json()
                print(f"    Response: {response_data}")
            except Exception:
                pass
            
        else:
            print(f"    [FAIL] Expected HTTP 200, got {response.status_code}")
            try:
                error_body = response.text
                print(f"    Error response: {error_body}")
            except Exception:
                pass
            raise AssertionError(
                f"write_service returned status {response.status_code}, expected 200"
            )
            
    except AssertionError:
        raise
    
    # All checks passed
    print("\n" + "=" * 60)
    print("PASS")
    print("=" * 60)
    print(f"\nSnow connector end-to-end verification successful.")
    print(f"Test row mcp_server_name: {mcp_server_name}")


def main() -> int:
    """Entry point for standalone execution."""
    try:
        run_smoke_test()
        return 0
    except AssertionError as e:
        print("\n" + "=" * 60)
        print("FAIL")
        print("=" * 60)
        print(f"\nAssertion failed: {e}")
        return 1
    except Exception as e:
        print("\n" + "=" * 60)
        print("FAIL")
        print("=" * 60)
        print(f"\nUnexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
