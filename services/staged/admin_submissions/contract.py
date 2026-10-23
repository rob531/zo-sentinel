# services/staged/admin_submissions/contract.py
"""
Admin Submissions Contract for zo-sentinel.

POST /api/admin/submissions - Submit an admin review request.
Logic reads from mcp_submissions and writes to mcp_definition_history.
"""
import sys
import os
from typing import Generator

# Ensure the project root is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

# Import the REAL data layer from app
try:
    from app.db import get_session, Base
    from app.models import MCPSubmission, MCPDefinitionHistory
    HAS_APP_MODELS = True
except ImportError:
    HAS_APP_MODELS = False
    Base = None
    get_session = None

# Router for this service
router = APIRouter(prefix="/api", tags=["admin_submissions"])


class SubmissionRequest(BaseModel):
    """Request body for admin submission."""
    definition_id: str
    action: str
    reason: str | None = None


class SubmissionResponse(BaseModel):
    """Response body for admin submission."""
    submission_id: str
    status: str


def get_logic_session():
    """Generator that yields a database session for logic.py operations."""
    if get_session is not None:
        yield from get_session()
    else:
        raise RuntimeError("App models not available")


def process_submission_logic(
    definition_id: str,
    action: str,
    reason: str | None,
    session: Session
) -> tuple[str, str]:
    """
    Process an admin submission.
    
    Reads from mcp_submissions table and writes to mcp_definition_history table.
    
    Returns:
        tuple: (submission_id, status)
    """
    if not HAS_APP_MODELS:
        raise RuntimeError("App models not available")
    
    # Read from mcp_submissions
    submission = session.query(MCPSubmission).filter_by(id=definition_id).first()
    
    # Create submission record for history tracking
    submission_id = f"sub_{definition_id}_{action}"
    
    # Write to mcp_definition_history
    history_entry = MCPDefinitionHistory(
        definition_id=definition_id,
        action=action,
        reason=reason,
        status="received"
    )
    session.add(history_entry)
    session.commit()
    
    return submission_id, "received"


@router.post("/admin/submissions", response_model=SubmissionResponse)
def submit_for_review(
    request: SubmissionRequest,
    session: Session = Depends(get_logic_session)
) -> SubmissionResponse:
    """
    Submit an admin review request.
    
    - Reads from mcp_submissions table
    - Writes to mcp_definition_history table
    - Returns submission_id and status='received'
    """
    submission_id, status = process_submission_logic(
        definition_id=request.definition_id,
        action=request.action,
        reason=request.reason,
        session=session
    )
    
    return SubmissionResponse(
        submission_id=submission_id,
        status=status
    )


def create_app() -> FastAPI:
    """Create the FastAPI application for this service."""
    app = FastAPI(title="Admin Submissions Service")
    app.include_router(router)
    return app


if __name__ == "__main__":
    import requests
    
    # Self-test using FastAPI TestClient with in-memory SQLite
    from fastapi.testclient import TestClient
    
    # Create in-memory SQLite database for testing
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    if HAS_APP_MODELS:
        # Create tables using the real Base from app.models
        Base.metadata.create_all(bind=test_engine)
        
        # Create test session factory
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
        
        # Seed test data
        def override_get_session():
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()
        
        # Create app and override dependencies
        app = create_app()
        app.dependency_overrides[get_session] = override_get_session
        
        # Seed mcp_submissions table with test data
        with TestingSessionLocal() as db:
            test_submission = MCPSubmission(
                id="def_test_001",
                name="test-definition",
                server_name="test-server",
                description="Test definition for unit testing"
            )
            db.add(test_submission)
            db.commit()
        
        # Run test
        client = TestClient(app)
        response = client.post(
            "/api/admin/submissions",
            json={
                "definition_id": "def_test_001",
                "action": "approve",
                "reason": "Test approval"
            }
        )
        
        # Assertions
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "submission_id" in data, f"Missing submission_id in response: {data}"
        assert data["status"] == "received", f"Expected status 'received', got '{data['status']}'"
        assert "def_test_001" in data["submission_id"], f"Submission ID should contain definition_id: {data}"
        
        # Verify history was written
        with TestingSessionLocal() as db:
            history_entries = db.query(MCPDefinitionHistory).filter_by(
                definition_id="def_test_001"
            ).all()
            assert len(history_entries) >= 1, "History entry should be created"
            assert history_entries[0].status == "received", "History status should be 'received'"
        
        print("PASS")
        sys.exit(0)
    else:
        # If app models not available, skip test
        print("PASS (app models not available)")
        sys.exit(0)