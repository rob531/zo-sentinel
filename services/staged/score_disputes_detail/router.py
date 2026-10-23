from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api", tags=["score_disputes_detail"])


class DisputeResponse(BaseModel):
    id: int
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: str
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


class DisputesListResponse(BaseModel):
    disputes: List[DisputeResponse]


@router.get("/disputes/{server_id}", response_model=DisputesListResponse)
def get_disputes(
    server_id: str,
    session=Depends(get_session),
) -> DisputesListResponse:
    disputes = (
        session.query(McpScoreDispute)
        .filter(McpScoreDispute.server_id == server_id)
        .order_by(McpScoreDispute.created_at.desc())
        .all()
    )
    return DisputesListResponse(
        disputes=[DisputeResponse.model_validate(d) for d in disputes]
    )


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    db = TestingSessionLocal()
    db.add(
        McpScoreDispute(
            server_id="test-srv-001",
            submitted_by="alice",
            proposed_overall_risk="high",
            proposed_axes='{"axis1": 0.9, "axis2": 0.8}',
            reason_category="incorrect_score",
            explanation="Score too low",
            status="pending",
            created_at=datetime(2024, 1, 3, 12, 0, 0),
            admin_note=None,
            resolved_at=None,
        )
    )
    db.add(
        McpScoreDispute(
            server_id="test-srv-001",
            submitted_by="bob",
            proposed_overall_risk="medium",
            proposed_axes='{"axis1": 0.5, "axis2": 0.6}',
            reason_category="outdated_data",
            explanation="Data is stale",
            status="approved",
            created_at=datetime(2024, 1, 2, 12, 0, 0),
            admin_note="Looks good",
            resolved_at=datetime(2024, 1, 2, 14, 0, 0),
        )
    )
    db.add(
        McpScoreDispute(
            server_id="test-srv-001",
            submitted_by="carol",
            proposed_overall_risk="low",
            proposed_axes='{"axis1": 0.2, "axis2": 0.3}',
            reason_category="other",
            explanation="Minor adjustment",
            status="rejected",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            admin_note="No change needed",
            resolved_at=datetime(2024, 1, 1, 14, 0, 0),
        )
    )
    db.commit()
    db.close()

    client = TestClient(test_app)
    response = client.get("/api/disputes/test-srv-001")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["disputes"]) == 3, f"Expected 3 disputes, got {len(data['disputes'])}"
    assert "status" in data["disputes"][0], "First dispute missing status field"

    print("PASS")