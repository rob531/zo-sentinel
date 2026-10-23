from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["disputes", "analytics"])


class DisputeSummary(BaseModel):
    total: int
    pending: int
    resolved: int
    approved: int
    rejected: int


class CategoryCount(BaseModel):
    reason_category: str
    count: int


class ServerDisputeCount(BaseModel):
    server_id: int
    dispute_count: int


class AnalyticsResponse(BaseModel):
    summary: DisputeSummary
    by_category: list[CategoryCount]
    top_servers: list[ServerDisputeCount]
    period_days: int


@router.get("/disputes/analytics", response_model=AnalyticsResponse)
async def get_disputes_analytics(
    period_days: int = 90,
    session: Session = Depends(get_session),
) -> AnalyticsResponse:
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    total_query = text("""
        SELECT COUNT(*) as total FROM McpScoreDispute
        WHERE created_at >= :cutoff
    """)
    total_result = session.execute(total_query, {"cutoff": cutoff}).fetchone()
    total = total_result[0] if total_result else 0

    status_query = text("""
        SELECT status, COUNT(*) as cnt FROM McpScoreDispute
        WHERE created_at >= :cutoff
        GROUP BY status
    """)
    status_results = session.execute(status_query, {"cutoff": cutoff}).fetchall()
    status_counts = {row[0]: row[1] for row in status_results}

    pending = status_counts.get("pending", 0)
    resolved = status_counts.get("resolved", 0)
    approved = status_counts.get("approved", 0)
    rejected = status_counts.get("rejected", 0)

    category_query = text("""
        SELECT reason_category, COUNT(*) as cnt FROM McpScoreDispute
        WHERE created_at >= :cutoff
        GROUP BY reason_category
        ORDER BY cnt DESC
    """)
    category_results = session.execute(category_query, {"cutoff": cutoff}).fetchall()
    by_category = [CategoryCount(reason_category=row[0], count=row[1]) for row in category_results]

    server_query = text("""
        SELECT server_id, COUNT(*) as cnt FROM McpScoreDispute
        WHERE created_at >= :cutoff
        GROUP BY server_id
        ORDER BY cnt DESC
        LIMIT 10
    """)
    server_results = session.execute(server_query, {"cutoff": cutoff}).fetchall()
    top_servers = [ServerDisputeCount(server_id=row[0], dispute_count=row[1]) for row in server_results]

    return AnalyticsResponse(
        summary=DisputeSummary(
            total=total,
            pending=pending,
            resolved=resolved,
            approved=approved,
            rejected=rejected,
        ),
        by_category=by_category,
        top_servers=top_servers,
        period_days=period_days,
    )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpScoreDispute (
                id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                proposed_overall_risk REAL,
                reason_category TEXT,
                status TEXT,
                created_at TIMESTAMP,
                explanation TEXT,
                proposed_axes TEXT,
                submitted_by INTEGER,
                admin_note TEXT,
                resolved_at TIMESTAMP
            )
        """))

    SessionLocal = sessionmaker(bind=engine)

    session = SessionLocal()
    now = datetime.utcnow()

    disputes = [
        (1, 1, 8.5, "false_negative", "pending", now - timedelta(days=5)),
        (2, 2, 7.0, "misclassification", "resolved", now - timedelta(days=10)),
        (3, 3, 9.0, "false_negative", "approved", now - timedelta(days=15)),
        (4, 1, 6.5, "outdated_score", "rejected", now - timedelta(days=20)),
        (5, 2, 8.0, "false_negative", "pending", now - timedelta(days=2)),
    ]

    for d in disputes:
        session.execute(
            text("""
                INSERT INTO McpScoreDispute 
                (id, server_id, proposed_overall_risk, reason_category, status, created_at)
                VALUES (:id, :server_id, :proposed_overall_risk, :reason_category, :status, :created_at)
            """),
            {
                "id": d[0],
                "server_id": d[1],
                "proposed_overall_risk": d[2],
                "reason_category": d[3],
                "status": d[4],
                "created_at": d[5],
            }
        )
    session.commit()
    session.close()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/disputes/analytics")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["summary"]["total"] == 5, f"Expected total=5, got {data['summary']['total']}"
    assert len(data["by_category"]) > 0, f"Expected by_category > 0, got {len(data['by_category'])}"

    print("PASS")