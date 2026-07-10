from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
import httpx
from app.db import get_session
from app.models import MCPSubmission

router = APIRouter()

class SubmissionCreate(BaseModel):
    mcp_name: str
    url: str
    description: Optional[str] = None
    requested_by: str

class SubmissionResponse(BaseModel):
    id: int
    mcp_name: str
    status: str
    created_at: str

def check_duplicate_submission(session, mcp_name: str, requested_by: str) -> bool:
    return session.query(MCPSubmission).filter(
        MCPSubmission.mcp_name == mcp_name,
        MCPSubmission.requested_by == requested_by
    ).first() is not None

@router.post("/submissions", response_model=SubmissionResponse, status_code=status.HTTP_201_CREATED)
async def create_submission(
    submission: SubmissionCreate,
    session=Depends(get_session)
):
    if not submission.mcp_name or not submission.url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing required fields: mcp_name and url are required"
        )

    if check_duplicate_submission(session, submission.mcp_name, submission.requested_by):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate submission: MCP name and requested_by must be unique"
        )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8772/write",
            json={
                "table": "mcp_submissions",
                "data": {
                    "mcp_name": submission.mcp_name,
                    "url": submission.url,
                    "description": submission.description,
                    "requested_by": submission.requested_by,
                    "status": "pending"
                }
            }
        )
        if response.status_code != status.HTTP_200_OK:
            raise HTTPException(
                status_code=response.status_code,
                detail="Failed to write to write_service"
            )

    return SubmissionResponse(
        id=response.json()["id"],
        mcp_name=submission.mcp_name,
        status="pending",
        created_at=response.json()["created_at"]
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    async def mock_write_service(*args, **kwargs):
        return {
            "id": 1,
            "created_at": "2023-01-01T00:00:00Z"
        }

    app.dependency_overrides[httpx.AsyncClient] = lambda: type('MockClient', (), {
        'post': lambda *args, **kwargs: type('MockResponse', (), {
            'status_code': 200,
            'json': lambda: mock_write_service(*args, **kwargs)
        })
    })()

    response = client.post(
        "/submissions",
        json={
            "mcp_name": "test_mcp",
            "url": "http://example.com",
            "description": "Test description",
            "requested_by": "test_user"
        }
    )
    assert response.status_code == 201
    assert "id" in response.json()

    response = client.post(
        "/submissions",
        json={
            "url": "http://example.com",
            "description": "Test description",
            "requested_by": "test_user"
        }
    )
    assert response.status_code == 422

    response = client.post(
        "/submissions",
        json={
            "mcp_name": "test_mcp",
            "url": "http://example.com",
            "description": "Test description",
            "requested_by": "test_user"
        }
    )
    assert response.status_code == 409

    print("PASS")