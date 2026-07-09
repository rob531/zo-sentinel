from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db import get_session
from app.models import MCPSubmission, MCPServerRegistry

router = APIRouter()

class SubmissionListResponse(BaseModel):
    submissions: List[dict]
    total: int
    page: int
    per_page: int

def get_paginated_submissions(
    db,
    page: int = 1,
    per_page: int = 10,
    status: Optional[str] = None,
    registry_source: Optional[str] = None
):
    query = select(MCPSubmission).options(joinedload(MCPSubmission.server))

    if status:
        query = query.where(MCPSubmission.status == status)
    if registry_source:
        query = query.where(MCPSubmission.registry_source == registry_source)

    total = db.scalar(select(query.statement.with_only_columns([query.statement.columns[MCPSubmission.id]]).count()))

    offset = (page - 1) * per_page
    submissions = db.execute(query.limit(per_page).offset(offset)).scalars().all()

    return {
        "submissions": [{
            "id": s.id,
            "server_id": s.server_id,
            "mcp_name": s.mcp_name,
            "registry_source": s.registry_source,
            "requested_by": s.requested_by,
            "submitted_at": s.submitted_at,
            "status": s.status,
            "server": {
                "id": s.server.id,
                "name": s.server.name,
                "description": s.server.description,
                "endpoint": s.server.endpoint,
                "owner": s.server.owner,
                "created_at": s.server.created_at,
                "updated_at": s.server.updated_at
            }
        } for s in submissions],
        "total": total,
        "page": page,
        "per_page": per_page
    }

@router.get("/admin/submissions", response_model=SubmissionListResponse)
async def list_submissions(
    db=Depends(get_session),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    registry_source: Optional[str] = Query(None)
):
    return get_paginated_submissions(db, page, per_page, status, registry_source)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        id=1,
        name="Test Server",
        description="Test Description",
        endpoint="http://test.com",
        owner="test@example.com",
        created_at="2023-01-01",
        updated_at="2023-01-01"
    )
    test_session.add(test_server)
    test_session.add(MCPSubmission(
        server_id=1,
        mcp_name="Test MCP",
        registry_source="test",
        requested_by="test@example.com",
        submitted_at="2023-01-01",
        status="pending"
    ))
    test_session.add(MCPSubmission(
        server_id=1,
        mcp_name="Approved MCP",
        registry_source="test",
        requested_by="test@example.com",
        submitted_at="2023-01-02",
        status="approved"
    ))
    test_session.commit()

    # Create test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test endpoint
    response = client.get("/admin/submissions?status=pending")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["submissions"]) == 1
    assert data["submissions"][0]["status"] == "pending"

    print("PASS")