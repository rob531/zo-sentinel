"""
MCP Submission Handler API

FastAPI handler for the MCP submissions portal that receives new MCP server requests
and writes them to mcp_submissions via write_service.

Port: 8794

# MARKER: MCP_SUBMISSION_HANDLER_WIRED - This handler is wired in api_gateway.py
"""

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, status, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
APP_PORT = 8794
MAX_BODY_SIZE = 64 * 1024  # 64KB

# Pydantic models for request/response validation
class SubmissionRequest(BaseModel):
    mcp_name: str = Field(..., min_length=1, max_length=255)
    registry_source: str = Field(..., min_length=1, max_length=255)
    mcp_url: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., max_length=2000)
    requested_by: str = Field(..., min_length=1, max_length=255)

class SubmissionResponse(BaseModel):
    submission_id: str
    status: str
    created_at: str
    mcp_name: Optional[str] = None
    registry_source: Optional[str] = None
    mcp_url: Optional[str] = None
    description: Optional[str] = None
    requested_by: Optional[str] = None

def sanitize_string(value: str) -> str:
    """Sanitize user-supplied strings to prevent injection attacks."""
    if not value:
        return ""
    # Remove null bytes and control characters
    sanitized = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
    # Trim excessive whitespace
    sanitized = re.sub(r'\s+', ' ', sanitized)
    return sanitized.strip()

def validate_url(url: str) -> bool:
    """Validate that the URL is well-formed."""
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(url_pattern.match(url))

# Create FastAPI app
app = FastAPI(title="MCP Submission Handler API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def check_duplicate_submission(mcp_name: str, mcp_url: str) -> bool:
    """Check if a submission already exists via write_service /query."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "table": "mcp_submissions",
                "filters": {
                    "mcp_name": mcp_name,
                    "mcp_url": mcp_url
                }
            },
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("exists", False) or bool(data.get("submissions"))
        return False
    except requests.exceptions.RequestException as e:
        logger.warning(f"Failed to check duplicates: {e}")
        return False

def write_submission(submission_data: Dict[str, Any]) -> Dict[str, Any]:
    """Write submission to mcp_submissions via write_service /write."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "mcp_submissions",
                "data": submission_data
            },
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write submission: {response.text}"
            )
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to write submission: {e}")
        raise HTTPException(
            status_code=503,
            detail="Database service unavailable"
        )

@app.post("/api/v1/submissions", response_model=SubmissionResponse, status_code=status.HTTP_201_CREATED)
async def create_submission(request: SubmissionRequest, http_request: Request):
    """Create a new MCP server submission.
    
    Accepts JSON body with fields: mcp_name, registry_source, mcp_url, description, requested_by.
    Returns 201 on success with submission_id.
    """
    # Check body size
    content_length = http_request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_SIZE:
        raise HTTPException(status_code=413, detail="Request body too large")
    
    # Sanitize all input fields
    sanitized_data = {
        "mcp_name": sanitize_string(request.mcp_name),
        "registry_source": sanitize_string(request.registry_source),
        "mcp_url": sanitize_string(request.mcp_url),
        "description": sanitize_string(request.description),
        "requested_by": sanitize_string(request.requested_by)
    }
    
    # Validate sanitized data is not empty
    for field, value in sanitized_data.items():
        if not value:
            raise HTTPException(status_code=400, detail=f"Field '{field}' cannot be empty after sanitization")
    
    # Validate URL format
    if not validate_url(sanitized_data["mcp_url"]):
        raise HTTPException(status_code=400, detail="Invalid URL format for mcp_url")
    
    # Check for duplicates via write_service /query
    if check_duplicate_submission(sanitized_data["mcp_name"], sanitized_data["mcp_url"]):
        raise HTTPException(
            status_code=409,
            detail="Submission already exists for this MCP server"
        )
    
    # Create submission
    submission_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    
    submission_data = {
        "submission_id": submission_id,
        "mcp_name": sanitized_data["mcp_name"],
        "registry_source": sanitized_data["registry_source"],
        "mcp_url": sanitized_data["mcp_url"],
        "description": sanitized_data["description"],
        "requested_by": sanitized_data["requested_by"],
        "status": "pending",
        "created_at": created_at
    }
    
    # Write to database
    write_submission(submission_data)
    
    logger.info(f"Created submission {submission_id} for MCP: {sanitized_data['mcp_name']}")
    
    return SubmissionResponse(
        submission_id=submission_id,
        status="pending",
        created_at=created_at,
        mcp_name=sanitized_data["mcp_name"],
        registry_source=sanitized_data["registry_source"],
        mcp_url=sanitized_data["mcp_url"],
        description=sanitized_data["description"],
        requested_by=sanitized_data["requested_by"]
    )

@app.get("/api/v1/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission(submission_id: str):
    """Get submission status by ID."""
    # Validate UUID format
    if not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', submission_id, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid submission ID format")
    
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "table": "mcp_submissions",
                "filters": {"submission_id": submission_id}
            },
            timeout=5
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Database query failed")
        
        data = response.json()
        
        # Check if submission found in different response formats
        if data.get("found"):
            submission = data["submission"]
            return SubmissionResponse(**submission)
        elif data.get("submissions") and len(data["submissions"]) > 0:
            submission = data["submissions"][0]
            return SubmissionResponse(**submission)
        else:
            raise HTTPException(status_code=404, detail="Submission not found")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to retrieve submission: {e}")
        raise HTTPException(status_code=503, detail="Database service unavailable")

@app.get("/api/v1/submissions", response_model=List[SubmissionResponse])
async def list_submissions(
    admin_key: str = Query(..., description="Admin API key for access"),
    limit: int = Query(100, ge=1, le=1000)
):
    """List all submissions (admin only)."""
    # Verify admin key - in production, use secure comparison
    expected_key = "admin-secret-key"  # In production, use env var
    if admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "table": "mcp_submissions",
                "limit": limit
            },
            timeout=10
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Database query failed")
        
        data = response.json()
        submissions = data.get("submissions", [])
        return [SubmissionResponse(**s) for s in submissions]
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to list submissions: {e}")
        raise HTTPException(status_code=503, detail="Database service unavailable")

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "mcp_submission_handler"}

if __name__ == "__main__":
    import uvicorn
    import threading
    import time
    import sqlite3
    import os
    from http.server import HTTPServer, BaseHTTPRequestHandler
    
    # Setup test database
    test_db_path = "/tmp/test_mcp_submissions.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    
    conn = sqlite3.connect(test_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mcp_submissions (
            submission_id TEXT PRIMARY KEY,
            mcp_name TEXT NOT NULL,
            registry_source TEXT NOT NULL,
            mcp_url TEXT NOT NULL,
            description TEXT,
            requested_by TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    
    print(f"Test database created at: {test_db_path}")
    
    # Mock write_service responses
    class MockWriteServiceHandler(BaseHTTPRequestHandler):
        db_path = test_db_path
        
        def log_message(self, format, *args):
            pass  # Suppress logging
        
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b'{}'
            data = json.loads(body.decode())
            
            if self.path == "/query":
                result = self.handle_query(data)
            elif self.path == "/write":
                result = self.handle_write(data)
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Not found"}).encode())
                return
            
            self.send_response(result["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result["body"]).encode())
        
        def handle_query(self, data):
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            table = data.get("table")
            filters = data.get("filters", {})
            
            if table == "mcp_submissions":
                conditions = []
                params = []
                
                if filters.get("submission_id"):
                    conditions.append("submission_id = ?")
                    params.append(filters["submission_id"])
                
                if filters.get("mcp_name"):
                    conditions.append("mcp_name = ?")
                    params.append(filters["mcp_name"])
                
                if filters.get("mcp_url"):
                    conditions.append("mcp_url = ?")
                    params.append(filters["mcp_url"])
                
                where_clause = " AND ".join(conditions) if conditions else "1=1"
                limit = data.get("limit", 100)
                
                cursor.execute(f"SELECT * FROM mcp_submissions WHERE {where_clause} LIMIT ?", params + [limit])
                rows = cursor.fetchall()
                
                columns = ["submission_id", "mcp_name", "registry_source", "mcp_url", 
                          "description", "requested_by", "status", "created_at"]
                
                conn.close()
                
                submissions = [dict(zip(columns, row)) for row in rows]
                
                if filters.get("submission_id") and submissions:
                    return {"status": 200, "body": {"found": True, "submission": submissions[0]}}
                elif filters.get("submission_id"):
                    return {"status": 200, "body": {"found": False, "submissions": []}}
                elif filters.get("mcp_name") and filters.get("mcp_url") and submissions:
                    return {"status": 200, "body": {"exists": True, "submissions": submissions}}
                else:
                    return {"status": 200, "body": {"submissions": submissions, "exists": len(submissions) > 0}}
            
            conn.close()
            return {"status": 200, "body": {"submissions": [], "exists": False}}
        
        def handle_write(self, data):
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            table = data.get("table")
            record = data.get("data")
            
            if table == "mcp_submissions":
                cursor.execute("""
                    INSERT INTO mcp_submissions 
                    (submission_id, mcp_name, registry_source, mcp_url, description, requested_by, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record["submission_id"],
                    record["mcp_name"],
                    record["registry_source"],
                    record["mcp_url"],
                    record["description"],
                    record["requested_by"],
                    record["status"],
                    record["created_at"]
                ))
                conn.commit()
                conn.close()
                return {"status": 200, "body": {"success": True}}
            
            conn.close()
            return {"status": 400, "body": {"error": "Unknown table"}}
    
    # Start mock service in background thread
    mock_server = HTTPServer(("127.0.0.1", 8772), MockWriteServiceHandler)
    mock_thread = threading.Thread(target=mock_server.serve_forever, daemon=True)
    mock_thread.start()
    print("Mock write_service started on port 8772")
    time.sleep(0.5)  # Wait for mock to start
    
    # Test the API
    print("\n=== Running API Tests ===\n")
    
    test_base_url = f"http://127.0.0.1:{APP_PORT}"
    
    # Start the FastAPI server in a thread
    import asyncio
    
    async def run_test():
        from httpx import AsyncClient
        
        # Start server
        config = uvicorn.Config(app, host="127.0.0.1", port=APP_PORT, log_level="error")
        server = uvicorn.Server(config)
        
        server_task = asyncio.create_task(server.serve())
        await asyncio.sleep(1)  # Wait for server to start
        
        try:
            async with AsyncClient(base_url=test_base_url, timeout=10.0) as client:
                # Test 1: Create a submission
                print("Test 1: Creating a new submission...")
                test_payload = {
                    "mcp_name": "Test MCP Server",
                    "registry_source": "test-registry",
                    "mcp_url": "https://example.com/mcp",
                    "description": "A test MCP server for validation",
                    "requested_by": "test_user"
                }
                
                response = await client.post("/api/v1/submissions", json=test_payload)
                
                if response.status_code != 201:
                    print(f"FAIL: Expected 201, got {response.status_code}")
                    print(f"Response: {response.text}")
                    return False
                
                result = response.json()
                submission_id = result.get("submission_id")
                
                if not submission_id:
                    print("FAIL: submission_id is null")
                    return False
                
                print(f"  Created submission: {submission_id}")
                
                # Test 2: Verify row was written to DB
                print("\nTest 2: Verifying row was written to database...")
                conn = sqlite3.connect(test_db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM mcp_submissions WHERE submission_id = ?", (submission_id,))
                row = cursor.fetchone()
                conn.close()
                
                if not row:
                    print("FAIL: Submission not found in database")
                    return False
                
                print(f"  Found row: submission_id={row[0]}, mcp_name={row[1]}, status={row[6]}")
                
                # Test 3: Get submission by ID
                print("\nTest 3: Getting submission by ID...")
                response = await client.get(f"/api/v1/submissions/{submission_id}")
                
                if response.status_code != 200:
                    print(f"FAIL: Expected 200, got {response.status_code}")
                    return False
                
                result = response.json()
                print(f"  Retrieved: submission_id={result['submission_id']}, status={result['status']}")
                
                # Test 4: Try to create duplicate
                print("\nTest 4: Testing duplicate detection...")
                response = await client.post("/api/v1/submissions", json=test_payload)
                
                if response.status_code != 409:
                    print(f"FAIL: Expected 409 for duplicate, got {response.status_code}")
                    return False
                
                print("  Correctly rejected duplicate submission with 409")
                
                # Test 5: List all submissions (admin)
                print("\nTest 5: Listing all submissions (admin)...")
                response = await client.get("/api/v1/submissions", params={"admin_key": "admin-secret-key"})
                
                if response.status_code != 200:
                    print(f"FAIL: Expected 200, got {response.status_code}")
                    return False
                
                result = response.json()
                if len(result) != 1:
                    print(f"FAIL: Expected 1 submission, got {len(result)}")
                    return False
                
                print(f"  Listed {len(result)} submission(s)")
                
                # Test 6: Invalid URL format
                print("\nTest 6: Testing URL validation...")
                bad_payload = test_payload.copy()
                bad_payload["mcp_url"] = "not-a-valid-url"
                response = await client.post("/api/v1/submissions", json=bad_payload)
                
                if response.status_code != 400:
                    print(f"FAIL: Expected 400 for invalid URL, got {response.status_code}")
                    return False
                
                print("  Correctly rejected invalid URL with 400")
                
                print("\n=== All Tests Passed ===")
                print("PASS")
                
                return True
                
        finally:
            server.should_exit = True
    
    # Run the async test
    try:
        asyncio.run(run_test())
    except ImportError:
        # Fallback to sync tests using requests if httpx not available
        print("Using requests for sync testing...")
        
        def run_sync_test():
            import requests
            
            # Start server in thread
            def start_server():
                uvicorn.run(app, host="127.0.0.1", port=APP_PORT, log_level="error")
            
            server_thread = threading.Thread(target=start_server, daemon=True)
            server_thread.start()
            time.sleep(2)  # Wait for server to start
            
            # Test 1: Create submission
            print("Test 1: Creating a new submission...")
            test_payload = {
                "mcp_name": "Test MCP Server",
                "registry_source": "test-registry",
                "mcp_url": "https://example.com/mcp",
                "description": "A test MCP server for validation",
                "requested_by": "test_user"
            }
            
            response = requests.post(f"{test_base_url}/api/v1/submissions", json=test_payload, timeout=5)
            
            if response.status_code != 201:
                print(f"FAIL: Expected 201, got {response.status_code}")
                print(f"Response: {response.text}")
                return False
            
            result = response.json()
            submission_id = result.get("submission_id")
            
            if not submission_id:
                print("FAIL: submission_id is null")
                return False
            
            print(f"  Created submission: {submission_id}")
            
            # Test 2: Verify row was written
            print("\nTest 2: Verifying row was written to database...")
            conn = sqlite3.connect(test_db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM mcp_submissions WHERE submission_id = ?", (submission_id,))
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                print("FAIL: Submission not found in database")
                return False
            
            print(f"  Found row in DB: submission_id={row[0]}, mcp_name={row[1]}")
            
            # Test 3: Get submission by ID
            print("\nTest 3: Getting submission by ID...")
            response = requests.get(f"{test_base_url}/api/v1/submissions/{submission_id}", timeout=5)
            
            if response.status_code != 200:
                print(f"FAIL: Expected 200, got {response.status_code}")
                return False
            
            result = response.json()
            print(f"  Retrieved: submission_id={result['submission_id']}")
            
            print("\n=== Tests Passed ===")
            print("PASS")
            return True
        
        run_sync_test()
    
    # Cleanup
    mock_server.shutdown()
    if os.path.exists(test_db_path):
        os.remove(test_db_path)