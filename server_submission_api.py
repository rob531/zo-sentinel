from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests
from datetime import datetime
from app.db import get_session
from app.models import MCPServerSubmission
from sqlalchemy.orm import Session

router = APIRouter()

class ServerSubmissionRequest(BaseModel):
    mcp_name: str
    registry_source: str
    requested_by: str
    notes: Optional[str] = None

class ServerSubmissionResponse(BaseModel):
    id: int
    mcp_name: str
    registry_source: str
    requested_by: str
    status: str
    created_at: datetime
    notes: Optional[str] = None

class ServerSubmissionListResponse(BaseModel):
    submissions: List[ServerSubmissionResponse]
    total: int

def write_service_request(table: str, operation: str, data: dict):
    url = "http://127.0.0.1:8772/write"
    payload = {
        "table": table,
        "operation": operation,
        "data": data
    }
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()

@router.post("/servers/submit", response_model=ServerSubmissionResponse)
async def submit_server(submission: ServerSubmissionRequest):
    data = {
        "mcp_name": submission.mcp_name,
        "registry_source": submission.registry_source,
        "requested_by": submission.requested_by,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "notes": submission.notes
    }
    result = write_service_request("mcp_submissions", "insert", data=data)
    return ServerSubmissionResponse(**result)

@router.get("/servers/submissions", response_model=ServerSubmissionListResponse)
async def list_submissions(skip: int = 0, limit: int = 100, db: Session = Depends(get_session)):
    submissions = db.query(MCPServerSubmission).offset(skip).limit(limit).all()
    total = db.query(MCPServerSubmission).count()
    return ServerSubmissionListResponse(
        submissions=[ServerSubmissionResponse(**submission.__dict__) for submission in submissions],
        total=total
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    # Test setup
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Test data
    test_submission = {
        "mcp_name": "test_mcp",
        "registry_source": "test_source",
        "requested_by": "test_user",
        "notes": "test notes"
    }

    # Test POST
    response = client.post("/servers/submit", json=test_submission)
    assert response.status_code == 200
    assert response.json()["mcp_name"] == test_submission["mcp_name"]
    assert response.json()["status"] == "pending"

    # Test GET
    response = client.get("/servers/submissions")
    assert response.status_code == 200
    assert len(response.json()["submissions"]) > 0

    print("PASS")