from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


router = APIRouter(prefix="/api/scoring/disputes", tags=["analytics"])


class StatusBreakdown(BaseModel):
    status: str
    count: int
    pct: float


class CategoryBreakdown(BaseModel):
    category: str
    count: int
    pct: float


class AnalyticsResponse(BaseModel):
    total: int
    open: int
    resolved: int
    by_status: List[StatusBreakdown]
    by_category: List[CategoryBreakdown]
    avg_resolution_hours: Optional[float]
    recent_7d: int
    recent_30d: int


@router.get("/analytics", response_model=AnalyticsResponse)
def get_disputes_analytics(session: Session = Depends(get_session)) -> AnalyticsResponse:
    now = datetime.utcnow()
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    total_query = session.query(func.count(McpScoreDispute.id))
    total = total_query.scalar() or 0

    open_query = session.query(func.count(McpScoreDispute.id)).filter(
        McpScoreDispute.status == "open"
    )
    open_count = open_query.scalar() or 0

    resolved_query = session.query(func.count(McpScoreDispute.id)).filter(
        McpScoreDispute.status == "resolved"
    )
    resolved_count = resolved_query.scalar() or 0

    status_breakdown_query = session.query(
        McpScoreDispute.status,
        func.count(McpScoreDispute.id).label("count")
    ).group_by(McpScoreDispute.status).all()

    by_status = []
    for row in status_breakdown_query:
        pct = (row.count / total * 100) if total > 0 else 0.0
        by_status.append(StatusBreakdown(status=row.status, count=row.count, pct=round(pct, 2)))

    category_breakdown_query = session.query(
        McpScoreDispute.reason_category,
        func.count(McpScoreDispute.id).label("count")
    ).group_by(McpScoreDispute.reason_category).all()

    by_category = []
    for row in category_breakdown_query:
        pct = (row.count / total * 100) if total > 0 else 0.0
        by_category.append(CategoryBreakdown(category=row.reason_category, count=row.count, pct=round(pct, 2)))

    avg_resolution_query = session.query(
        func.avg(
            func.extract('epoch', McpScoreDispute.resolved_at) - func.extract('epoch', McpScoreDispute.created_at)
        ) / 3600
    ).filter(
        McpScoreDispute.status == "resolved",
        McpScoreDispute.resolved_at.isnot(None)
    )
    avg_resolution_hours = avg_resolution_query.scalar()

    recent_7d_query = session.query(func.count(McpScoreDispute.id)).filter(
        McpScoreDispute.created_at >= seven_days_ago
    )
    recent_7d = recent_7d_query.scalar() or 0

    recent_30d_query = session.query(func.count(McpScoreDispute.id)).filter(
        McpScoreDispute.created_at >= thirty_days_ago
    )
    recent_30d = recent_30d_query.scalar() or 0

    return AnalyticsResponse(
        total=total,
        open=open_count,
        resolved=resolved_count,
        by_status=by_status,
        by_category=by_category,
        avg_resolution_hours=round(avg_resolution_hours, 2) if avg_resolution_hours is not None else None,
        recent_7d=recent_7d,
        recent_30d=recent_30d
    )


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_session = TestingSessionLocal()

    for i in range(1, 6):
        server = McpServerRegistry(
            server_id=f"srv_{i}",
            name=f"Test Server {i}",
            url=f"https://server{i}.example.com",
            registry_source="test",
            risk_tier="medium",
            verdict="clean",
            confidence=0.85,
            trust_score=75.0,
            first_seen=datetime.utcnow() - timedelta(days=90),
            last_seen=datetime.utcnow() - timedelta(days=i),
            last_scanned=datetime.utcnow() - timedelta(days=i),
            last_assessed=datetime.utcnow() - timedelta(days=i),
            scan_count=10
        )
        test_session.add(server)

    test_session.commit()

    statuses = ["open", "open", "resolved", "resolved", "pending", "open", "resolved", "open", "resolved", "resolved", "pending", "open"]
    categories = ["incorrect_risk", "missing_axis", "incorrect_risk", "outdated_score", "incorrect_risk", "missing_axis", "outdated_score", "incorrect_risk", "missing_axis", "outdated_score", "incorrect_risk", "missing_axis"]

    for i in range(12):
        hours_ago_created = (i + 1) * 5
        created = datetime.utcnow() - timedelta(hours=hours_ago_created)
        resolved = None
        if statuses[i] == "resolved":
            hours_ago_resolved = hours_ago_created - 2
            resolved = datetime.utcnow() - timedelta(hours=hours_ago_resolved)

        dispute = McpScoreDispute(
            server_id=f"srv_{(i % 5) + 1}",
            submitted_by=f"user_{i}",
            proposed_overall_risk="high",
            proposed_axes={"axis1": 0.8, "axis2": 0.6},
            reason_category=categories[i],
            explanation=f"Test dispute explanation {i}",
            status=statuses[i],
            admin_note=f"Admin note {i}" if statuses[i] == "resolved" else None,
            created_at=created,
            resolved_at=resolved
        )
        test_session.add(dispute)

    test_session.commit()

    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/scoring/disputes/analytics")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()

    assert data["total"] >= 12, f"Expected total >= 12, got {data['total']}"

    status_sum = sum(s["count"] for s in data["by_status"])
    assert status_sum == data["total"], f"by_status sum {status_sum} != total {data['total']}"

    assert isinstance(data["avg_resolution_hours"], (int, float)), f"avg_resolution_hours should be numeric, got {type(data['avg_resolution_hours'])}"

    print("PASS")
    sys.exit(0)