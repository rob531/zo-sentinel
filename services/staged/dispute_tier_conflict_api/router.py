"""HTTP surface for the dispute tier conflict API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import list_conflicts

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class ConflictResponse(BaseModel):
    dispute_id: int
    server_id: str
    name: str | None
    current_risk_tier: str | None
    proposed_overall_risk: str
    status: str
    reason_category: str
    proposed_axes: Any | None
    created_at: datetime | None


class ConflictsResponse(BaseModel):
    status: str
    count: int
    conflicts: list[ConflictResponse]


@router.get("/conflicts", response_model=ConflictsResponse)
def get_conflicts(
    status: str | None = Query(default=None),
    limit: int = Query(default=50),
    db: Session = Depends(get_session),
) -> ConflictsResponse:
    """Return disputes whose proposed tier differs from the registry tier."""
    bounded_limit = max(1, min(limit, 200))
    rows = list_conflicts(db, status=status, limit=bounded_limit)

    if isinstance(rows, dict):
        conflict_rows = rows.get("conflicts", [])
    else:
        conflict_rows = rows

    conflicts = [ConflictResponse.model_validate(row) for row in conflict_rows]
    return ConflictsResponse(status="ok", count=len(conflicts), conflicts=conflicts)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpScoreDispute, McpServerRegistry

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    testing_session.add_all(
        [
            McpServerRegistry(server_id="server-1", name="Server 1", risk_tier="high"),
            McpServerRegistry(server_id="server-2", name="Server 2", risk_tier="low"),
            McpServerRegistry(server_id="server-3", name="Server 3", risk_tier=None),
            McpScoreDispute(
                id=1,
                server_id="server-1",
                proposed_overall_risk="HIGH",
                proposed_axes={"overall": "high"},
                status="open",
                reason_category="model_review",
                explanation="Proposed tier matches the registry.",
                submitted_by="tester",
            ),
            McpScoreDispute(
                id=2,
                server_id="server-2",
                proposed_overall_risk="critical",
                proposed_axes={"overall": "critical"},
                status="open",
                reason_category="model_review",
                explanation="Proposed tier conflicts with the registry.",
                submitted_by="tester",
            ),
            McpScoreDispute(
                id=3,
                server_id="server-3",
                proposed_overall_risk="medium",
                proposed_axes={"overall": "medium"},
                status="closed",
                reason_category="model_review",
                explanation="The registry has no current tier.",
                submitted_by="tester",
            ),
        ]
    )
    testing_session.commit()

    def override_get_session():
        testing_session.close()
        session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/conflicts")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["conflicts"][0]["dispute_id"] == 2
        assert payload["conflicts"][0]["current_risk_tier"] == "low"
        assert payload["conflicts"][0]["proposed_overall_risk"] == "critical"

        response = client.get("/api/disputes/conflicts?status=open")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert all(item["status"] == "open" for item in payload["conflicts"])

    print("PASS")