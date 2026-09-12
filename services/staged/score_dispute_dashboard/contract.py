from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
from sqlalchemy import func, and_

app = FastAPI()

class DisputeSummary(BaseModel):
    total: int
    by_status: dict
    disputes: List[dict]

class DisputeDetail(BaseModel):
    id: int
    server_id: str
    server_name: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: dict
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str]
    created_at: str
    resolved_at: Optional[str]

@app.get("/api/score-disputes/summary", response_model=DisputeSummary)
async def get_dispute_summary(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session)
):
    query = session.query(McpScoreDispute)
    if status:
        query = query.filter(McpScoreDispute.status == status)

    total = query.count()
    by_status = {
        status: count
        for status, count in session.query(
            McpScoreDispute.status,
            func.count(McpScoreDispute.id)
        ).group_by(McpScoreDispute.status).all()
    }

    disputes = query.limit(limit).offset(offset).all()
    disputes_data = [
        {
            "id": d.id,
            "server_id": d.server_id,
            "submitted_by": d.submitted_by,
            "proposed_overall_risk": d.proposed_overall_risk,
            "reason_category": d.reason_category,
            "status": d.status,
            "created_at": d.created_at.isoformat(),
            "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
            "admin_note": d.admin_note
        }
        for d in disputes
    ]

    return {
        "total": total,
        "by_status": by_status,
        "disputes": disputes_data
    }

@app.get("/api/score-disputes/{dispute_id}", response_model=DisputeDetail)
async def get_dispute_detail(
    dispute_id: int,
    session: Session = Depends(get_session)
):
    dispute = session.query(McpScoreDispute).filter(McpScoreDispute.id == dispute_id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    server = session.query(McpServerRegistry.name).filter(McpServerRegistry.server_id == dispute.server_id).first()

    return {
        "id": dispute.id,
        "server_id": dispute.server_id,
        "server_name": server.name if server else None,
        "submitted_by": dispute.submitted_by,
        "proposed_overall_risk": dispute.proposed_overall_risk,
        "proposed_axes": dispute.proposed_axes,
        "reason_category": dispute.reason_category,
        "explanation": dispute.explanation,
        "status": dispute.status,
        "admin_note": dispute.admin_note,
        "created_at": dispute.created_at.isoformat(),
        "resolved_at": dispute.resolved_at.isoformat() if dispute.resolved_at else None
    }

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from datetime import datetime, timedelta

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        # Create test servers
        server1 = McpServerRegistry(
            server_id="server1",
            name="Test Server 1",
            risk_tier="high",
            verdict="malicious",
            last_scanned=datetime.now()
        )
        server2 = McpServerRegistry(
            server_id="server2",
            name="Test Server 2",
            risk_tier="medium",
            verdict="suspicious",
            last_scanned=datetime.now()
        )
        server3 = McpServerRegistry(
            server_id="server3",
            name="Test Server 3",
            risk_tier="low",
            verdict="benign",
            last_scanned=datetime.now()
        )
        session.add_all([server1, server2, server3])

        # Create test disputes
        now = datetime.now()
        dispute1 = McpScoreDispute(
            server_id="server1",
            submitted_by="user1",
            proposed_overall_risk="low",
            proposed_axes={"axis1": "value1", "axis2": "value2"},
            reason_category="incorrect_assessment",
            explanation="This server is actually low risk",
            status="open",
            created_at=now
        )
        dispute2 = McpScoreDispute(
            server_id="server2",
            submitted_by="user2",
            proposed_overall_risk="high",
            proposed_axes={"axis1": "value3", "axis2": "value4"},
            reason_category="false_positive",
            explanation="This is a false positive",
            status="open",
            created_at=now - timedelta(days=1)
        )
        dispute3 = McpScoreDispute(
            server_id="server3",
            submitted_by="user1",
            proposed_overall_risk="medium",
            proposed_axes={"axis1": "value5", "axis2": "value6"},
            reason_category="incorrect_assessment",
            explanation="Risk tier should be medium",
            status="resolved",
            admin_note="Resolved after review",
            created_at=now - timedelta(days=2),
            resolved_at=now - timedelta(days=1)
        )
        session.add_all([dispute1, dispute2, dispute3])
        session.commit()

    # Run tests
    client = TestClient(app)

    # Test summary endpoint
    response = client.get("/api/score-disputes/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["by_status"]["open"] == 2
    assert data["by_status"]["resolved"] == 1
    assert len(data["disputes"]) == 3

    # Test detail endpoint
    response = client.get("/api/score-disputes/1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["status"] == "open"
    assert data["server_name"] == "Test Server 1"

    response = client.get("/api/score-disputes/3")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 3
    assert data["status"] == "resolved"
    assert data["server_name"] == "Test Server 3"

    print("PASS")