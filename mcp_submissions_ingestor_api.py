#!/usr/bin/env python3
"""
mcp_submissions_ingestor_api.py — FastAPI REST API for submitting MCP servers for InfoSec review.
"""

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEALTH_SERVICE_URL = "http://127.0.0.1:8773"
SERVICE_PORT = 8785
HEARTBEAT_INTERVAL = 60

# Allowed registry sources
ALLOWED_REGISTRY_SOURCES = {"npm", "github", "smithery", "pypi", "docker", "manual"}

# In-memory store for submissions (supplements write_service persistence)
_submissions_store: dict = {}
_store_lock = threading.Lock()

# FastAPI app
app = FastAPI(
    title="MCP Submissions Ingestor API",
    description="Public-facing API for submitting MCP servers for InfoSec review",
    version="1.0.0",
)


def write_to_mcp_submissions(row_data: dict) -> str:
    """
    Write a row to mcp_submissions via write_service HTTP endpoint.
    Uses the pattern: {'table': 'mcp_submissions', 'rows': {...}, 'wait': True}
    """
    payload = {
        "table": "mcp_submissions",
        "rows": row_data,
        "wait": True,
    }
    
    response = requests.post(
        WRITE_SERVICE_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    
    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"write_service failed: {response.status_code} - {response.text}",
        )
    
    return response.json().get("submission_id", row_data.get("submission_id"))


def validate_submission(mcp_name: str, registry_source: str, definition_url: str, requested_by: str) -> None:
    """Validate submission parameters."""
    if not mcp_name or not mcp_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mcp_name is required and cannot be empty",
        )
    
    if registry_source not in ALLOWED_REGISTRY_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"registry_source must be one of: {', '.join(sorted(ALLOWED_REGISTRY_SOURCES))}",
        )
    
    if not definition_url or not definition_url.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="definition_url is required and cannot be empty",
        )
    
    if not requested_by or not requested_by.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="requested_by is required and cannot be empty",
        )


def send_heartbeat() -> None:
    """Send heartbeat to service_health."""
    try:
        payload = {
            "service": "mcp_submissions_ingestor",
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        requests.post(
            HEALTH_SERVICE_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    except Exception:
        pass  # Heartbeat failures should not crash the service


def heartbeat_loop() -> None:
    """Background thread for sending heartbeats."""
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_heartbeat()


# Start heartbeat thread
heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
heartbeat_thread.start()


@app.get("/health")
def health_check() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(
        content={
            "status": "ok",
            "service": "mcp_submissions_ingestor",
        },
        status_code=200,
    )


@app.post("/submissions")
def create_submission(
    mcp_name: str,
    registry_source: str,
    definition_url: str,
    requested_by: str,
    description: Optional[str] = None,
    homepage_url: Optional[str] = None,
) -> JSONResponse:
    """
    Submit a new MCP server for InfoSec review.
    
    Writes to mcp_submissions and returns the submission_id.
    """
    # Validate inputs
    validate_submission(mcp_name, registry_source, definition_url, requested_by)
    
    # Generate submission ID
    submission_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    
    # Build row data
    row_data = {
        "submission_id": submission_id,
        "mcp_name": mcp_name.strip(),
        "registry_source": registry_source,
        "definition_url": definition_url.strip(),
        "requested_by": requested_by.strip(),
        "status": "pending",
        "created_at": created_at,
        "updated_at": created_at,
    }
    
    if description:
        row_data["description"] = description.strip()
    if homepage_url:
        row_data["homepage_url"] = homepage_url.strip()
    
    # Write to mcp_submissions via write_service
    write_to_mcp_submissions(row_data)
    
    # Also store locally for retrieval
    with _store_lock:
        _submissions_store[submission_id] = row_data.copy()
    
    return JSONResponse(
        content={
            "submission_id": submission_id,
            "status": "pending",
            "created_at": created_at,
        },
        status_code=201,
    )


@app.get("/submissions/{submission_id}")
def get_submission(submission_id: str) -> JSONResponse:
    """
    Retrieve submission status by ID.
    """
    # Try local store first
    with _store_lock:
        if submission_id in _submissions_store:
            return JSONResponse(
                content=_submissions_store[submission_id],
                status_code=200,
            )
    
    # Fallback: query write_service for the submission
    try:
        response = requests.get(
            f"{WRITE_SERVICE_URL}/query",
            params={"table": "mcp_submissions", "submission_id": submission_id},
            timeout=10,
        )
        
        if response.status_code == 200:
            data = response.json()
            if data:
                with _store_lock:
                    _submissions_store[submission_id] = data[0] if isinstance(data, list) else data
                return JSONResponse(
                    content=data[0] if isinstance(data, list) else data,
                    status_code=200,
                )
    except Exception:
        pass
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Submission {submission_id} not found",
    )


@app.get("/submissions")
def list_submissions(
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
) -> JSONResponse:
    """
    List all submissions with pagination.
    """
    # Query write_service for all submissions
    try:
        offset = (page - 1) * page_size
        response = requests.get(
            f"{WRITE_SERVICE_URL}/query",
            params={
                "table": "mcp_submissions",
                "limit": page_size,
                "offset": offset,
            },
            timeout=10,
        )
        
        if response.status_code == 200:
            data = response.json()
            submissions = data if isinstance(data, list) else []
            
            return JSONResponse(
                content={
                    "submissions": submissions,
                    "page": page,
                    "page_size": page_size,
                    "total": len(submissions),
                },
                status_code=200,
            )
    except Exception:
        pass
    
    # Fallback to local store
    with _store_lock:
        all_submissions = list(_submissions_store.values())
    
    total = len(all_submissions)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paginated = all_submissions[start_idx:end_idx]
    
    return JSONResponse(
        content={
            "submissions": paginated,
            "page": page,
            "page_size": page_size,
            "total": total,
        },
        status_code=200,
    )


def run() -> None:
    """Run the FastAPI server using uvicorn."""
    import uvicorn
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=SERVICE_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    import socket
    import urllib.request
    import urllib.error
    import concurrent.futures
    
    def find_available_port(start_port: int = 8785) -> int:
        """Find an available port starting from start_port."""
        port = start_port
        while port < start_port + 100:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                    return port
            except OSError:
                port += 1
        raise RuntimeError("No available port found")
    
    # Override the port with an available one
    actual_port = find_available_port(SERVICE_PORT)
    
    # Store original port value for the module
    import sys
    module = sys.modules[__name__]
    original_run = run
    
    def test_run():
        import uvicorn
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=actual_port,
            log_level="error",
        )
    
    # Start the server in a background thread
    server_thread = threading.Thread(target=test_run, daemon=True)
    server_thread.start()
    
    # Wait for server to start
    time.sleep(2)
    
    # Run tests
    base_url = f"http://127.0.0.1:{actual_port}"
    
    try:
        # Test 1: Health check
        health_resp = requests.get(f"{base_url}/health", timeout=5)
        assert health_resp.status_code == 200, f"Health check failed: {health_resp.status_code}"
        assert health_resp.json()["status"] == "ok"
        assert health_resp.json()["service"] == "mcp_submissions_ingestor"
        print("  ✓ Health check passed")
        
        # Test 2: Create a submission
        test_submission = {
            "mcp_name": "test-mcp-server",
            "registry_source": "npm",
            "definition_url": "https://registry.npmjs.org/test-mcp/latest",
            "requested_by": "test-user@example.com",
            "description": "A test MCP server for validation",
            "homepage_url": "https://github.com/example/test-mcp",
        }
        
        create_resp = requests.post(
            f"{base_url}/submissions",
            json=test_submission,
            timeout=5,
        )
        assert create_resp.status_code == 201, f"Create submission failed: {create_resp.status_code} - {create_resp.text}"
        
        result = create_resp.json()
        assert "submission_id" in result, "Missing submission_id in response"
        assert result["status"] == "pending"
        assert "created_at" in result
        submission_id = result["submission_id"]
        print(f"  ✓ Create submission passed (id: {submission_id})")
        
        # Test 3: Get submission by ID
        get_resp = requests.get(f"{base_url}/submissions/{submission_id}", timeout=5)
        assert get_resp.status_code == 200, f"Get submission failed: {get_resp.status_code}"
        
        get_data = get_resp.json()
        assert get_data["submission_id"] == submission_id
        assert get_data["mcp_name"] == test_submission["mcp_name"]
        assert get_data["registry_source"] == test_submission["registry_source"]
        print("  ✓ Get submission passed")
        
        # Test 4: List submissions with pagination
        list_resp = requests.get(f"{base_url}/submissions", timeout=5)
        assert list_resp.status_code == 200, f"List submissions failed: {list_resp.status_code}"
        
        list_data = list_resp.json()
        assert "submissions" in list_data
        assert "page" in list_data
        assert "page_size" in list_data
        assert "total" in list_data
        print("  ✓ List submissions passed")
        
        # Test 5: Validation - empty mcp_name
        bad_resp = requests.post(
            f"{base_url}/submissions",
            json={
                "mcp_name": "",
                "registry_source": "npm",
                "definition_url": "https://example.com/def.json",
                "requested_by": "user@test.com",
            },
            timeout=5,
        )
        assert bad_resp.status_code == 400, f"Validation for empty mcp_name failed: {bad_resp.status_code}"
        print("  ✓ Validation for empty mcp_name passed")
        
        # Test 6: Validation - invalid registry_source
        bad_resp2 = requests.post(
            f"{base_url}/submissions",
            json={
                "mcp_name": "valid-name",
                "registry_source": "invalid_source",
                "definition_url": "https://example.com/def.json",
                "requested_by": "user@test.com",
            },
            timeout=5,
        )
        assert bad_resp2.status_code == 400, f"Validation for invalid registry_source failed: {bad_resp2.status_code}"
        print("  ✓ Validation for invalid registry_source passed")
        
        print("\nPASS")
        
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)