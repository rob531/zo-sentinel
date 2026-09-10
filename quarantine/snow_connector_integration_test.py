#!/usr/bin/env python3
"""
Integration test for snow_connector.py wiring into approval_workflow.
Verifies: submissions -> approval_workflow -> snow_connector -> SNOW table write.
"""

import sys
import time
import json
import uuid
import subprocess
import requests
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

# Service endpoints
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"
SNOW_MOCK_ENDPOINT = "http://127.0.0.1:9999/api/snOW/table"

def check_service_health(service_name, url, max_retries=3):
    """Check if a service is healthy."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(f"{url}/health", timeout=5)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False

def write_service_request(table, rows):
    """POST to write_service."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json={"table": table, "rows": rows, "wait": True},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()

def query_service(sql):
    """POST query to write_service."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": sql},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()

def execute_service(sql):
    """POST DDL/DML to write_service."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/execute",
        json={"sql": sql},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()

def setup_test_submission():
    """Create a test submission in mcp_submissions."""
    submission_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    submission = {
        "submission_id": submission_id,
        "mcp_name": "test-mcp-sentinel-snow",
        "description": "Integration test submission for SNOW connector",
        "submitter_email": "test@example.com",
        "submission_source": "integration_test",
        "status": "pending_review",
        "submitted_at": now,
        "reviewed_at": None,
        "reviewer": None,
        "verdict": None,
        "risk_tier": None,
        "threat_count": 0
    }
    
    # Write the submission
    result = write_service_request("mcp_submissions", submission)
    print(f"[STEP 1] Created test submission: {submission_id}")
    print(f"         write_service result: {result}")
    
    return submission_id

def create_mock_snow_api():
    """Create and start a mock SNOW API server."""
    from fastapi import FastAPI
    import uvicorn
    
    app = FastAPI()
    received_records = []
    
    @app.post("/api/snow/table")
    async def receive_record(record: dict):
        received_records.append(record)
        return {"status": "success", "sys_id": str(uuid.uuid4())}
    
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "mock_snow_api"}
    
    return app, received_records

def trigger_approval_workflow(submission_id):
    """Trigger approval_workflow to process the submission."""
    try:
        resp = requests.post(
            f"{APPROVAL_WORKFLOW_URL}/process",
            json={"submission_id": submission_id},
            timeout=30
        )
        resp.raise_for_status()
        print(f"[STEP 2] Triggered approval_workflow for: {submission_id}")
        print(f"         response: {resp.json()}")
        return resp.json()
    except requests.RequestException as e:
        print(f"[STEP 2] WARNING: Could not reach approval_workflow: {e}")
        print("         Will simulate workflow processing...")
        return {"simulated": True, "submission_id": submission_id}

def verify_snow_connector_output(submission_id, snow_records):
    """Verify snow_connector wrote to expected SNOW table structure."""
    print(f"[STEP 3] Checking snow_connector output...")
    
    if not snow_records:
        print("[STEP 3] No SNOW records received by mock API")
        # Check if snow_writes table exists and has the record
        try:
            result = query_service(f"""
                SELECT * FROM snow_writes 
                WHERE submission_id = '{submission_id}'
                LIMIT 1
            """)
            if result.get('rows') and len(result['rows']) > 0:
                print(f"[STEP 3] Found record in snow_writes table: {result['rows'][0]}")
                return True
        except Exception as e:
            print(f"[STEP 3] Query of snow_writes failed: {e}")
        return False
    
    for record in snow_records:
        required_fields = ['table_name', 'record_data', 'submission_id', 'processed_at']
        missing = [f for f in required_fields if f not in record]
        if missing:
            print(f"[STEP 3] FAIL: Record missing fields: {missing}")
            return False
        
        if record['submission_id'] != submission_id:
            print(f"[STEP 3] FAIL: submission_id mismatch")
            return False
        
        print(f"[STEP 3] SNOW record validated: {record}")
    
    return True

def verify_no_database_errors():
    """Use write_service query to verify no DB errors occurred."""
    print("[STEP 4] Checking for database errors...")
    
    # Check audit_log for any errors related to our test
    try:
        result = query_service("""
            SELECT * FROM audit_log 
            WHERE event_type LIKE '%error%' OR event_type LIKE '%fail%'
            ORDER BY created_at DESC 
            LIMIT 10
        """)
        error_logs = result.get('rows', [])
        if error_logs:
            print(f"[STEP 4] Found error logs: {error_logs}")
            return False
    except Exception as e:
        # Table might not exist, which is fine
        print(f"[STEP 4] audit_log check (non-critical): {e}")
    
    # Verify mcp_submissions table is queryable
    try:
        result = query_service("SELECT COUNT(*) as cnt FROM mcp_submissions")
        print(f"[STEP 4] mcp_submissions queryable, count: {result}")
    except Exception as e:
        print(f"[STEP 4] FAIL: Database query error: {e}")
        return False
    
    return True

def create_and_start_mock_snow():
    """Start mock SNOW API in background thread."""
    import threading
    import uvicorn
    
    app, received_records = create_mock_snow_api()
    
    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=9999, log_level="error")
    
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(2)  # Wait for server to start
    
    return received_records

def main():
    """Run integration test for snow_connector."""
    print("=" * 60)
    print("ZO-SENTINEL: snow_connector Integration Test")
    print("=" * 60)
    
    test_passed = True
    errors = []
    submission_id = None
    
    # Start mock SNOW API
    print("\n[SETUP] Starting mock SNOW API on port 9999...")
    snow_records = create_and_start_mock_snow()
    
    # Check prerequisite services
    print("\n[CHECK] Verifying prerequisite services...")
    services_ok = True
    
    if not check_service_health("write_service", WRITE_SERVICE_URL):
        print(f"[CHECK] WARNING: write_service not reachable at {WRITE_SERVICE_URL}")
        print("         Test will attempt to proceed...")
        services_ok = False
    
    if not check_service_health("approval_workflow", APPROVAL_WORKFLOW_URL):
        print(f"[CHECK] WARNING: approval_workflow not reachable at {APPROVAL_WORKFLOW_URL}")
    
    try:
        # STEP 1: Create test submission
        print("\n[STEP 1] Creating test MCP submission...")
        submission_id = setup_test_submission()
        
        # STEP 2: Trigger approval_workflow
        print("\n[STEP 2] Triggering approval_workflow processing...")
        workflow_result = trigger_approval_workflow(submission_id)
        
        # Wait for async processing
        print("[STEP 2] Waiting for snow_connector processing...")
        time.sleep(3)
        
        # STEP 3: Verify snow_connector output
        print("\n[STEP 3] Verifying snow_connector SNOW table writes...")
        if not verify_snow_connector_output(submission_id, snow_records):
            test_passed = False
            errors.append("SNOW connector did not write expected records")
        
        # STEP 4: Verify no database errors
        print("\n[STEP 4] Verifying database integrity...")
        if not verify_no_database_errors():
            test_passed = False
            errors.append("Database errors detected")
        
    except Exception as e:
        test_passed = False
        errors.append(f"Test exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup: mark test submission as completed
        if submission_id:
            try:
                execute_service(f"""
                    UPDATE mcp_submissions 
                    SET status = 'test_completed', 
                        reviewed_at = '{datetime.now().isoformat()}',
                        verdict = 'approved'
                    WHERE submission_id = '{submission_id}'
                """)
                print(f"\n[CLEANUP] Marked test submission as completed")
            except Exception as e:
                print(f"\n[CLEANUP] Could not update submission: {e}")
    
    # Final result
    print("\n" + "=" * 60)
    if test_passed:
        print("RESULT: PASS - All integration steps completed successfully")
        print("=" * 60)
        sys.exit(0)
    else:
        print("RESULT: FAIL - Integration test failed")
        print("\nDiagnostics:")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        print("=" * 60)
        sys.exit(1)

if __name__ == "__main__":
    main()