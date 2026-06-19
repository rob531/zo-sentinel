#!/usr/bin/env python3
"""
MCP Submission Handler Daemon

Polls mcp_server_registry for new candidates (status='candidate'),
promotes approved ones to mcp_submissions, and exposes FastAPI endpoints
for manual promotion by analysts.

Uses write_service at http://127.0.0.1:8772/write for persistence.
Sends heartbeat to service_health every 60s.
"""

import os
import time
import json
import sqlite3
import logging
import threading
import tempfile
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

import requests
from fastapi import FastAPI, HTTPException, Query

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
HEALTH_SERVICE_URL = "http://127.0.0.1:8773/health"
DATABASE_PATH = os.environ.get("MCP_REGISTRY_DB", "mcp_registry.db")
POLL_INTERVAL = 300   # 5 minutes
HEARTBEAT_INTERVAL = 60  # 1 minute

# FastAPI app
app = FastAPI(title="MCP Submission Handler")

# Thread safety for DB access
_db_lock = threading.Lock()

# Module-level DB path (for testing override)
_db_path: str = DATABASE_PATH


def set_db_path(path: str) -> None:
    """Set the database path (for testing)."""
    global _db_path
    _db_path = path


@contextmanager
def get_db_connection():
    """Get a thread-safe database connection."""
    with _db_lock:
        conn = sqlite3.connect(_db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def init_database() -> None:
    """Initialize database schema."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Registry table: stores candidate MCPs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                mcp_id INTEGER PRIMARY KEY,
                mcp_name TEXT NOT NULL,
                registry_source TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                status TEXT DEFAULT 'candidate'
            )
        """)
        
        # Submissions table: stores promoted MCPs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mcp_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mcp_id INTEGER NOT NULL,
                mcp_name TEXT NOT NULL,
                registry_source TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                notes TEXT,
                submitted_at TEXT NOT NULL
            )
        """)
        
        conn.commit()
        logger.info("Database initialized")


def fetch_pending_candidates() -> List[Dict[str, Any]]:
    """Fetch all candidate MCPs from registry that haven't been submitted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.mcp_id, r.mcp_name, r.registry_source, r.first_seen
            FROM mcp_server_registry r
            WHERE r.status = 'candidate'
              AND NOT EXISTS (
                  SELECT 1 FROM mcp_submissions s WHERE s.mcp_id = r.mcp_id
              )
            ORDER BY r.first_seen ASC
        """)
        return [dict(row) for row in cursor.fetchall()]


def fetch_submissions() -> List[Dict[str, Any]]:
    """Fetch all submissions from mcp_submissions."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, mcp_id, mcp_name, registry_source,
                   requested_by, notes, submitted_at
            FROM mcp_submissions
            ORDER BY submitted_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]


def promote_mcp(mcp_id: int, requested_by: str, notes: str = "") -> Dict[str, Any]:
    """
    Promote a candidate MCP from registry into mcp_submissions.
    
    1. Verify candidate exists and is in 'candidate' status
    2. Verify not already submitted
    3. Insert into mcp_submissions
    4. POST to write_service for external persistence
    5. Return the written row
    
    Args:
        mcp_id: The MCP ID to promote
        requested_by: Username of the requester
        notes: Optional notes for the promotion
        
    Returns:
        dict with submitted_at and mcp_name
        
    Raises:
        ValueError: If candidate not found or already submitted
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Fetch candidate from registry
        cursor.execute("""
            SELECT mcp_id, mcp_name, registry_source, first_seen
            FROM mcp_server_registry
            WHERE mcp_id = ? AND status = 'candidate'
        """, (mcp_id,))
        candidate = cursor.fetchone()
        
        if not candidate:
            raise ValueError(
                f"Candidate MCP {mcp_id} not found or not in candidate status"
            )
        
        candidate = dict(candidate)
        
        # Check if already submitted
        cursor.execute(
            "SELECT 1 FROM mcp_submissions WHERE mcp_id = ?",
            (mcp_id,)
        )
        if cursor.fetchone():
            raise ValueError(f"MCP {mcp_id} has already been submitted")
        
        # Prepare submission record
        submitted_at = datetime.utcnow().isoformat()
        row = {
            "mcp_name": candidate["mcp_name"],
            "registry_source": candidate["registry_source"],
            "requested_by": requested_by,
            "notes": notes,
            "submitted_at": submitted_at
        }
        
        # Insert into mcp_submissions
        cursor.execute("""
            INSERT INTO mcp_submissions
                (mcp_id, mcp_name, registry_source, requested_by, notes, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            candidate["mcp_id"],
            row["mcp_name"],
            row["registry_source"],
            row["requested_by"],
            row["notes"],
            row["submitted_at"]
        ))
        conn.commit()
        
        # Prepare payload for write_service
        payload = {
            "table": "mcp_submissions",
            "rows": [row]
        }
        
        # POST to write_service
        try:
            response = requests.post(
                WRITE_SERVICE_URL,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"Promoted MCP {mcp_id} to write_service")
        except requests.RequestException as e:
            # Log but don't fail - local write succeeded
            logger.warning(
                f"write_service POST failed for MCP {mcp_id}: {e}"
            )
        
        # Return the written row
        return {
            "mcp_name": row["mcp_name"],
            "submitted_at": row["submitted_at"],
            "mcp_id": candidate["mcp_id"],
            "registry_source": row["registry_source"],
            "requested_by": row["requested_by"],
            "notes": row["notes"]
        }


def poll_and_promote() -> int:
    """
    Poll registry for new candidates and promote them.
    
    Returns:
        Number of candidates promoted
    """
    candidates = fetch_pending_candidates()
    promoted = 0
    
    for candidate in candidates:
        try:
            promote_mcp(
                candidate["mcp_id"],
                requested_by="daemon",
                notes="Auto-promoted by mcp_submission_handler"
            )
            promoted += 1
            logger.info(f"Auto-promoted MCP {candidate['mcp_id']}: {candidate['mcp_name']}")
        except Exception as e:
            logger.error(f"Failed to auto-promote MCP {candidate['mcp_id']}: {e}")
    
    return promoted


def heartbeat_loop() -> None:
    """Send periodic heartbeats to service_health."""
    while True:
        try:
            requests.post(
                HEALTH_SERVICE_URL,
                json={
                    "service": "mcp_submission_handler",
                    "timestamp": datetime.utcnow().isoformat()
                },
                timeout=5
            )
            logger.debug("Heartbeat sent to service_health")
        except requests.RequestException as e:
            logger.warning(f"Heartbeat failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def polling_loop() -> None:
    """Main polling loop - checks registry every POLL_INTERVAL seconds."""
    while True:
        try:
            count = poll_and_promote()
            if count > 0:
                logger.info(f"Polling cycle complete: {count} candidates promoted")
        except Exception as e:
            logger.error(f"Polling cycle failed: {e}")
        time.sleep(POLL_INTERVAL)


def run() -> None:
    """Start the daemon: polling loop, heartbeat thread, and FastAPI server."""
    # Initialize database
    init_database()
    
    # Start background threads
    threading.Thread(target=poll_loop, daemon=True, name="PollingLoop").start()
    threading.Thread(target=heartbeat_loop, daemon=True, name="HeartbeatLoop").start()
    
    logger.info("MCP Submission Handler starting on port 8784")
    
    # Run FastAPI
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8784, log_level="info")


# =============================================================================
# FastAPI Routes
# =============================================================================

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "mcp_submission_handler"}


@app.post("/promote/{mcp_id}")
async def promote_endpoint(
    mcp_id: int,
    requested_by: str = Query(..., description="Username of requester"),
    notes: str = Query("", description="Optional notes for promotion")
):
    """
    Manually promote a candidate MCP to mcp_submissions.
    
    Promotes the MCP and returns the written row on success.
    """
    try:
        result = promote_mcp(mcp_id, requested_by, notes)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Promotion failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/pending")
async def get_pending():
    """Get all pending candidate MCPs awaiting promotion."""
    candidates = fetch_pending_candidates()
    return {"count": len(candidates), "candidates": candidates}


@app.get("/submissions")
async def get_submissions():
    """Get all MCP submissions."""
    submissions = fetch_submissions()
    return {"count": len(submissions), "submissions": submissions}


# =============================================================================
# Self-Test
# =============================================================================

def self_test() -> bool:
    """
    Self-test that validates promote_mcp with mocked write_service.
    
    Creates test database, inserts test candidate, calls promote_mcp,
    and verifies returned dict contains submitted_at and mcp_name.
    """
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    
    print("\n=== MCP Submission Handler Self-Test ===")
    
    # Create temporary test database
    test_db = tempfile.mktemp(suffix=".db")
    set_db_path(test_db)
    
    # Mock write_service server
    mock_response_data = {"status": "success", "rows_written": 1}
    
    class MockWriteHandler(BaseHTTPRequestHandler):
        """Handler that captures write_service requests."""
        received_request = None
        
        def do_POST(self):
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            MockWriteHandler.received_request = json.loads(body)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(mock_response_data).encode())
        
        def log_message(self, format, *args):
            pass  # Suppress server logs
    
    # Start mock server
    mock_server = HTTPServer(('127.0.0.1', 8772), MockWriteHandler)
    mock_thread = threading.Thread(target=mock_server.handle_request)
    mock_thread.start()
    
    try:
        # Setup test database and candidate
        init_database()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO mcp_server_registry
                    (mcp_id, mcp_name, registry_source, first_seen, status)
                VALUES (?, ?, ?, ?, ?)
            """, (999999, 'Test MCP Alpha', 'test_registry', 
                  datetime.utcnow().isoformat(), 'candidate'))
            conn.commit()
        
        print(f"✓ Test candidate MCP 999999 inserted")
        
        # Call promote_mcp (will POST to our mock server)
        result = promote_mcp(999999, 'test_user', 'test notes')
        
        print(f"✓ promote_mcp returned: {result}")
        
        # Assertions
        assert 'submitted_at' in result, "FAIL: Missing 'submitted_at' in result"
        print(f"✓ Result contains 'submitted_at': {result['submitted_at']}")
        
        assert 'mcp_name' in result, "FAIL: Missing 'mcp_name' in result"
        print(f"✓ Result contains 'mcp_name': {result['mcp_name']}")
        
        assert result['mcp_name'] == 'Test MCP Alpha', \
            f"FAIL: Expected 'Test MCP Alpha', got '{result['mcp_name']}'"
        print(f"✓ mcp_name value correct")
        
        # Verify write_service was called correctly
        req = MockWriteHandler.received_request
        assert req is not None, "FAIL: write_service was not called"
        assert req.get('table') == 'mcp_submissions', \
            f"FAIL: Wrong table, got '{req.get('table')}'"
        assert 'rows' in req, "FAIL: Missing 'rows' in payload"
        assert len(req['rows']) == 1, f"FAIL: Expected 1 row, got {len(req['rows'])}"
        print(f"✓ write_service POST payload correct")
        
        # Verify local database
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM mcp_submissions WHERE mcp_id = ?",
                (999999,)
            )
            row = cursor.fetchone()
            assert row is not None, "FAIL: Submission not in local DB"
            print(f"✓ Submission persisted to local database")
        
        print("\n=== All assertions passed ===")
        return True
        
    except Exception as e:
        print(f"\nFAIL: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        set_db_path(DATABASE_PATH)
        mock_server.server_close()
        if os.path.exists(test_db):
            os.remove(test_db)
        print("\n=== Self-Test Complete ===")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        success = self_test()
        sys.exit(0 if success else 1)
    else:
        run()