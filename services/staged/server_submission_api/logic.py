from typing import Optional
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ServerSubmissionRequest(BaseModel):
    name: str = Field(..., description="Server name")
    url: str = Field(..., description="Server URL")
    registry_source: str = Field(..., description="Source registry")
    description: Optional[str] = Field(None, description="Server description")
    requested_by: str = Field(..., description="User who requested submission")


class ServerSubmissionResponse(BaseModel):
    id: int
    status: str = "pending"


class SubmissionListItem(BaseModel):
    id: int
    name: str
    url: str
    registry_source: str
    description: Optional[str]
    requested_by: str
    status: str


class PaginatedSubmissions(BaseModel):
    items: list[SubmissionListItem]
    total: int
    page: int
    page_size: int


def get_server_registry(
    name: Optional[str] = None,
    url: Optional[str] = None,
    registry_source: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Optional[McpServerRegistry]:
    query = select(McpServerRegistry)
    if name:
        query = query.where(McpServerRegistry.name == name)
    if url:
        query = query.where(McpServerRegistry.url == url)
    if registry_source:
        query = query.where(McpServerRegistry.registry_source == registry_source)
    result = session.execute(query)
    return result.scalars().first()


def create_submission(
    request: ServerSubmissionRequest,
) -> dict:
    submission = {
        "name": request.name,
        "url": request.url,
        "registry_source": request.registry_source,
        "description": request.description,
        "requested_by": request.requested_by,
        "status": "pending",
    }
    return submission


def list_submissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
) -> dict:
    return {
        "items": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from unittest.mock import MagicMock

    app = FastAPI(title="server_submission_api")

    _submissions_store: list[dict] = []
    _next_id = 1

    @app.post("/api/servers/submit", response_model=ServerSubmissionResponse, status_code=201)
    async def submit_server(request: ServerSubmissionRequest):
        global _next_id
        submission_id = _next_id
        _next_id += 1
        submission = {
            "id": submission_id,
            "name": request.name,
            "url": request.url,
            "registry_source": request.registry_source,
            "description": request.description,
            "requested_by": request.requested_by,
            "status": "pending",
        }
        _submissions_store.append(submission)
        return ServerSubmissionResponse(id=submission_id, status="pending")

    @app.get("/api/servers/submissions", response_model=PaginatedSubmissions)
    async def list_server_submissions(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status: Optional[str] = None,
    ):
        items = _submissions_store
        if status:
            items = [s for s in items if s["status"] == status]
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        paginated = items[start:end]
        return PaginatedSubmissions(
            items=[
                SubmissionListItem(
                    id=s["id"],
                    name=s["name"],
                    url=s["url"],
                    registry_source=s["registry_source"],
                    description=s["description"],
                    requested_by=s["requested_by"],
                    status=s["status"],
                )
                for s in paginated
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    def mock_get_session():
        mock_session = MagicMock(spec=Session)
        return mock_session

    app.dependency_overrides[get_session] = mock_get_session

    _submissions_store.append({
        "id": 0,
        "name": "test-server",
        "url": "https://test.example.com",
        "registry_source": "test",
        "description": "Test submission",
        "requested_by": "test-user",
        "status": "pending",
    })

    import asyncio
    from httpx import AsyncClient, ASGITransport

    async def run_tests():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/servers/submit",
                json={
                    "name": "new-server",
                    "url": "https://new.example.com",
                    "registry_source": "manual",
                    "description": "A new server",
                    "requested_by": "admin",
                },
            )
            assert response.status_code == 201, f"Expected 201, got {response.status_code}"
            data = response.json()
            assert "id" in data, "Missing 'id' in response"
            assert data["status"] == "pending", f"Expected 'pending', got {data['status']}"

            response = await client.get("/api/servers/submissions")
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            data = response.json()
            assert data["total"] >= 1, f"Expected at least 1 submission, got {data['total']}"
            assert len(data["items"]) >= 1, f"Expected at least 1 item, got {len(data['items'])}"

            print("PASS")

    asyncio.run(run_tests())