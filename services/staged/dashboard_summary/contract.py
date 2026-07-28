# services/staged/dashboard_summary/contract.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import mcp_server_registry, mcp_llm_axis_scores

router = APIRouter(prefix="/api", tags=["dashboard_summary"])


class TierStats(BaseModel):
    count: int
    avg_score: float | None


class DashboardSummaryResponse(BaseModel):
    summary: dict[str, TierStats]


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
    status_code=status.HTTP_200_OK,
)
def get_dashboard_summary(db: Session = Depends(get_session)):
    # Join server registry with axis scores and aggregate per risk tier
    stmt = (
        select(
            mcp_server_registry.c.risk_tier,
            func.count(mcp_server_registry.c.id).label("cnt"),
            func.avg(mcp_llm_axis_scores.c.score).label("avg_score"),
        )
        .select_from(
            mcp_server_registry.join(
                mcp_llm_axis_scores,
                mcp_server_registry.c.id == mcp_llm_axis_scores.c.server_id,
                isouter=True,
            )
        )
        .group_by(mcp_server_registry.c.risk_tier)
    )
    result = db.execute(stmt).all()

    summary: dict[str, TierStats] = {}
    for tier, cnt, avg_score in result:
        summary[tier] = TierStats(count=cnt, avg_score=avg_score)

    return DashboardSummaryResponse(summary=summary)


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    # ----------------------------------------------------------------------
    # Self‑test (acceptance)
    # ----------------------------------------------------------------------
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from fastapi.testclient import TestClient

    # In‑memory SQLite for the test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Override the dependency to use the test session
    def get_test_session() -> Session:
        with SessionLocal() as sess:
            yield sess

    app.dependency_overrides[get_session] = get_test_session

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed data
    with SessionLocal() as sess:
        # Insert servers with various risk tiers
        servers = [
            {"id": 1, "risk_tier": "low"},
            {"id": 2, "risk_tier": "low"},
            {"id": 3, "risk_tier": "medium"},
            {"id": 4, "risk_tier": "high"},
            {"id": 5, "risk_tier": "critical"},
        ]
        sess.execute(mcp_server_registry.insert(), servers)

        # Insert axis scores (one per server)
        scores = [
            {"id": 1, "server_id": 1, "score": 10.0},
            {"id": 2, "server_id": 2, "score": 20.0},
            {"id": 3, "server_id": 3, "score": 30.0},
            {"id": 4, "server_id": 4, "score": 40.0},
            {"id": 5, "server_id": 5, "score": 50.0},
        ]
        sess.execute(mcp_llm_axis_scores.insert(), scores)
        sess.commit()

    client = TestClient(app)

    resp = client.get("/api/dashboard/summary")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    summary = data.get("summary", {})

    # Verify that the "low" tier has the expected count (2)
    low_stats = summary.get("low")
    if not low_stats or low_stats["count"] != 2:
        print("FAIL: low tier count mismatch", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)