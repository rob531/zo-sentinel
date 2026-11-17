"""HTTP router for the vulnerability-link backlog service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unlinked_backlog

router = APIRouter(prefix="/api", tags=["vuln"])


class UnlinkedServer(BaseModel):
    server_id: str
    name: str | None = None
    registry_source: str | None = None
    risk_tier: str | None = None
    last_seen: datetime | None = None
    link_count: int = 0


class UnlinkedBacklogResponse(BaseModel):
    generated_at: datetime
    total_unlinked: int
    limit: int
    items: list[UnlinkedServer]


@router.get("/vuln/backlog/unlinked", response_model=UnlinkedBacklogResponse)
def get_unlinked_backlog_endpoint(
    db: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=500),
    ecosystem: str | None = Query(default=None),
) -> UnlinkedBacklogResponse:
    result: Any = get_unlinked_backlog(db, limit=limit, ecosystem=ecosystem)

    if isinstance(result, dict):
        raw_items = result.get("items", [])
        total_unlinked = result.get("total_unlinked", len(raw_items))
    elif isinstance(result, tuple):
        raw_items, total_unlinked = result
    else:
        raw_items = result
        total_unlinked = len(raw_items)

    items = [UnlinkedServer.model_validate(item) for item in raw_items]
    return UnlinkedBacklogResponse(
        generated_at=datetime.now(timezone.utc),
        total_unlinked=int(total_unlinked),
        limit=limit,
        items=items,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry, VulnAdvisory, VulnLink

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    VulnAdvisory.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    test_db = TestSession()
    test_db.add_all(
        [
            McpServerRegistry(server_id="linked-server", name="Linked server"),
            McpServerRegistry(server_id="unlinked-server-1", name="Unlinked server 1"),
            McpServerRegistry(server_id="unlinked-server-2", name="Unlinked server 2"),
            VulnAdvisory(id=1, feed="nvd", source_url="https://example.test/advisory/1"),
            VulnLink(
                id=1,
                advisory_id=1,
                server_id="linked-server",
                match_basis="package",
                match_value="example",
                match_confidence=1.0,
            ),
        ]
    )
    test_db.commit()

    def override_get_session():
        yield test_db

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    response = TestClient(test_app).get("/api/vuln/backlog/unlinked")
    payload = response.json()
    assert response.status_code == 200, response.text
    assert payload["total_unlinked"] == 2, payload
    assert all(item["server_id"] != "linked-server" for item in payload["items"]), payload
    print("PASS")