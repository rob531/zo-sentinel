from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel
import logging

# Configure logging
logger = logging.getLogger(__name__)

# Request model for submission data
class SubmissionData(BaseModel):
    server_name: str
    llm_scores: Optional[list] = None
    metadata: Optional[dict] = None

router = APIRouter(
    prefix="/api",
    tags=["submission_intake"],
    responses={400: {"description": "Invalid input"}}
)

@router.post("/submissions")
async def create_submission(
    submission: SubmissionData,
    db_session=Depends(get_session)
):
    """
    Public endpoint for submission intake.
    Creates a new server registry entry and associated LLM scores.
    """
    try:
        # Create server registry entry
        new_server = McpServerRegistry(
            name=submission.server_name,
            status="pending"
        )
        db_session.add(new_server)
        db_session.flush()  # Get the ID before committing

        # Create LLM scores if provided
        if submission.llm_scores:
            for score_data in submission.llm_scores:
                new_score = McpLlmAxisScore(
                    server_id=new_server.id,
                    axis=score_data.get("axis"),
                    value=score_data.get("value"),
                    timestamp=score_data.get("timestamp")
                )
                db_session.add(new_score)

        db_session.commit()

        logger.info(f"Successfully created submission for server {new_server.id}")
        return {
            "status": "success",
            "server_id": new_server.id,
            "message": "Submission received and processing"
        }

    except Exception as e:
        db_session.rollback()
        logger.error(f"Error processing submission: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Error processing submission"
        )

if __name__ == "__main__":
    # Self-test setup
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Create in-memory test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    # Create test app with dependency override
    test_app = FastAPI()
    test_app.include_router(router)

    # Override get_session for testing
    def test_get_session():
        return Session(test_engine)

    test_app.dependency_overrides[get_session] = test_get_session

    # Simple self-test
    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    test_response = client.post(
        "/api/submissions",
        json={
            "server_name": "test_server",
            "llm_scores": [
                {"axis": "accuracy", "value": 95, "timestamp": "2026-08-10T00:00:00"}
            ]
        }
    )

    print(f"Test result: {test_response.json()}")
    assert test_response.status_code == 200
