from typing import Optional
from collections import defaultdict

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session, Base
from app.models import McpScoreDispute, McpServerRegistry


class DisputeSummary(BaseModel):
    total: int
    by_status: dict
    disputes: list


class DisputeDetail(BaseModel):
    id: int
    server_id: str
    server_name: Optional[str]
    submitted_by: str
    proposed_overall_risk: Optional[str]
    proposed_axes: Optional[dict]
    reason_category: Optional[str]
    explanation: Optional[str]
    status: str
    admin_note: Optional[str]
    created_at: str
    resolved_at: Optional[str]


class DisputeListItem(BaseModel):
    id: int
    server_id: str
    submitted_by: str
    proposed_overall_risk: Optional[str]
    reason_category: Optional[str]
    status: str
    created_at: str
    resolved_at: Optional[str]
    admin_note: Optional[str]


app = FastAPI()


@app.get("/api/score-disputes/summary")
def get_summary(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    query = select(McpScoreDispute)
    if status:
        query = query.where(McpScoreDispute.status == status)

    total_result = session.execute(
        select(func.count()).select_from(McpScoreDispute)
    ).scalar()

    by_status_result = session.execute(
        select(McpScoreDispute.status, func.count())
        .group_by(McpScoreDispute.status)
    ).all()
    by_status = {row[0]: row[1] for row in by_status_result}

    query = query.order_by(McpScoreDispute.created_at.desc()).limit(limit).offset(offset)
    disputes_result = session.execute(query).scalars().all()

    disputes = []
    for d in disputes_result:
        disputes.append({
            "id": d.id,
            "server_id": d.server_id,
            "submitted_by": d.submitted_by,
            "proposed_overall_risk": d.proposed_overall_risk,
            "reason_category": d.reason_category,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
            "admin_note": d.admin_note,
        })

    return {"total": total_result or 0, "by_status": by_status, "disputes": disputes}


@app.get("/api/score-disputes/{dispute_id}")
def get_dispute(
    dispute_id: int,
    session: Session = Depends(get_session),
) -> dict:
    result = session.execute(
        select(McpScoreDispute).where(McpScoreDispute.id == dispute_id)
    ).scalar_one_or_none()

    if not result:
        raise HTTPException(status_code=404, detail="Dispute not found")

    server_name = None
    if result.server_id:
        server = session.execute(
            select(McpServerRegistry.name).where(McpServerRegistry.server_id == result.server_id)
        ).scalar_one_or_none()
        server_name = server

    import json
    proposed_axes = None
    if result.proposed_axes:
        try:
            proposed_axes = json.loads(result.proposed_axes)
        except Exception:
            proposed_axes = result.proposed_axes

    return {
        "id": result.id,
        "server_id": result.server_id,
        "server_name": server_name,
        "submitted_by": result.submitted_by,
        "proposed_overall_risk": result.proposed_overall_risk,
        "proposed_axes": proposed_axes,
        "reason_category": result.reason_category,
        "explanation": result.explanation,
        "status": result.status,
        "admin_note": result.admin_note,
        "created_at": result.created_at.isoformat() if result.created_at else None,
        "resolved_at": result.resolved_at.isoformat() if result.resolved_at else None,
    }


if __name__ == "__main__":
    import json
    from datetime import datetime, timezone

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    now = datetime.now(timezone.utc)
    server_id_1 = "srv-001"
    server_id_2 = "srv-002"

    server1 = McpServerRegistry(
        server_id=server_id_1,
        name="Production API Server",
        url="https://api.example.com",
        registry_source="manual",
    )
    server2 = McpServerRegistry(
        server_id=server_id_2,
        name="Staging API Server",
        url="https://staging.example.com",
        registry_source="manual",
    )
    test_session.add_all([server1, server2])
    test_session.commit()

    dispute1 = McpScoreDispute(
        server_id=server_id_1,
        submitted_by="admin@example.com",
        proposed_overall_risk="medium",
        proposed_axes=json.dumps({"security": 6, "reliability": 7}),
        reason_category="score_too_high",
        explanation="Risk score appears inflated",
        status="open",
        created_at=now,
    )
    dispute2 = McpScoreDispute(
        server_id=server_id_2,
        submitted_by="user@example.com",
        proposed_overall_risk="low",
        proposed_axes=json.dumps({"security": 4, "reliability": 8}),
        reason_category="score_incorrect",
        explanation="Incorrect scoring criteria",
        status="open",
        created_at=now,
    )
    resolved_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    dispute3 = McpScoreDispute(
        server_id=server_id_1,
        submitted_by="admin@example.com",
        proposed_overall_risk="low",
        reason_category="outdated",
        explanation="Old data",
        status="resolved",
        admin_note="Reviewed and closed",
        created_at=now,
        resolved_at=resolved_time,
    )
    test_session.add_all([dispute1, dispute2, dispute3])
    test_session.commit()

    that_app = FastAPI()
    that_app.include_router(app.router)

    def override_get_session():
        return test_session

    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)

    resp = client.get("/api/score-disputes/summary")
    data = resp.json()
    assert data["total"] == 3, f"Expected total=3, got {data['total']}"
    assert data["by_status"]["open"] == 2, f"Expected open=2, got {data['by_status'].get('open')}"

    resp_detail = client.get(f"/api/score-disputes/{dispute1.id}")
    detail = resp_detail.json()
    assert detail["status"] == "open", f"Expected open, got {detail['status']}"

    print("PASS")