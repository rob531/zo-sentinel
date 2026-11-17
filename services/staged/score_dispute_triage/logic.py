"""Data and computation layer for the score-dispute triage service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


class DisputeTriageItem(BaseModel):
    id: int
    server_id: str
    server_name: str
    current_risk_tier: str
    proposed_overall_risk: str
    reason_category: str
    submitted_by: str
    age_days: int
    status: str


class DisputeTriageResponse(BaseModel):
    count: int
    disputes: List[DisputeTriageItem]


def _age_in_days(created_at: datetime) -> int:
    """Return whole elapsed days, clamped to zero for future timestamps."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    age = datetime.now(timezone.utc) - created_at
    return max(0, age.days)


def triage_disputes(
    db: Session,
    status: Literal["open", "pending"] = "open",
    limit: int = 50,
) -> List[dict]:
    """Return score disputes joined to the current server registry record."""
    if status not in ("open", "pending"):
        return []
    if limit < 1:
        return []
    limit = min(limit, 200)

    rows = db.execute(
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpScoreDispute.submitted_by,
            McpScoreDispute.created_at,
            McpScoreDispute.status,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(McpScoreDispute.status == status)
        .order_by(McpScoreDispute.created_at.asc())
        .limit(limit)
    ).all()

    return [
        {
            "id": int(row.id),
            "server_id": row.server_id,
            "server_name": row.name or "unknown",
            "current_risk_tier": row.risk_tier or "unknown",
            "proposed_overall_risk": row.proposed_overall_risk,
            "reason_category": row.reason_category,
            "submitted_by": row.submitted_by,
            "age_days": _age_in_days(row.created_at),
            "status": row.status,
        }
        for row in rows
    ]


def get_dispute_triage(
    db: Session = Depends(get_session),
    status: Literal["open", "pending"] = Query("open"),
    limit: int = Query(50, ge=1, le=200),
) -> DisputeTriageResponse:
    """FastAPI endpoint handler for the dispute triage queue."""
    disputes = triage_disputes(db, status=status, limit=limit)
    return DisputeTriageResponse(count=len(disputes), disputes=disputes)


app = FastAPI()
app.get(
    "/api/disputes/triage",
    response_model=DisputeTriageResponse,
)(get_dispute_triage)


if __name__ == "__main__":
    from datetime import timedelta
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)

    db = SessionLocal()
    try:
        db.add(
            McpServerRegistry(
                server_id="server-1",
                name="Example Server",
                risk_tier="high",
            )
        )
        db.add(
            McpScoreDispute(
                id=1,
                server_id="server-1",
                submitted_by="reviewer@example.test",
                proposed_overall_risk="high",
                reason_category="false_positive",
                explanation="The score does not reflect current usage.",
                status="open",
                created_at=datetime.now(timezone.utc) - timedelta(days=3),
            )
        )
        db.add(
            McpScoreDispute(
                id=2,
                server_id="server-1",
                submitted_by="reviewer@example.test",
                proposed_overall_risk="low",
                reason_category="score_update",
                explanation="Already resolved.",
                status="resolved",
                created_at=datetime.now(timezone.utc) - timedelta(days=10),
                resolved_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.get(
        "/api/disputes/triage",
        response_model=DisputeTriageResponse,
    )(get_dispute_triage)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/triage?status=open&limit=50")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["disputes"][0]["id"] == 1
        assert payload["disputes"][0]["current_risk_tier"] == "high"
        assert payload["disputes"][0]["age_days"] >= 0
        assert all(item["status"] in ("open", "pending") for item in payload["disputes"])
        assert all(item["id"] != 2 for item in payload["disputes"])

    print("PASS")