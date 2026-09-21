from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk"])


class RiskSummaryResponse(BaseModel):
    total_servers: int
    by_tier: dict[str, int]


def get_risk_summary(db: Session = Depends(get_session)) -> RiskSummaryResponse:
    """Compute risk summary from McpServerRegistry."""
    # Total count
    total = db.execute(
        select(func.count()).select_from(McpServerRegistry)
    ).scalar() or 0

    # Group by risk_tier
    rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()

    by_tier = {tier: count for tier, count in rows}

    return RiskSummaryResponse(total_servers=total, by_tier=by_tier)


@router.get("/risk/summary", response_model=RiskSummaryResponse)
def risk_summary(db: Session = Depends(get_session)) -> RiskSummaryResponse:
    return get_risk_summary(db)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    from fastapi import FastAPI
    from app.models import Base
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory test store
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    # Seed 10 servers with varied tiers
    tiers = ["critical", "high", "high", "medium", "medium", "medium", "low", "low", "low", "low"]
    for i, tier in enumerate(tiers, 1):
        srv = McpServerRegistry(
            server_id=f"srv_{i:03d}",
            name=f"TestServer{i}",
            url=f"http://localhost:{8000+i}",
            registry_source="test",
            risk_tier=tier,
            trust_score=0.5 + (i * 0.05),
            confidence=0.8,
            verdict="unknown",
            scan_count=0,
        )
        test_session.add(srv)
    test_session.commit()

    # Override dependency
    def _get_test_session():
        try:
            yield test_session
        finally:
            pass

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient

    that_app = app
    that_app.dependency_overrides[get_session] = _get_test_session
    client = TestClient(that_app)

    response = client.get("/api/risk/summary")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["total_servers"] == 10, f"Expected 10, got {data['total_servers']}"

    by_tier = data["by_tier"]
    expected = {"critical": 1, "high": 2, "medium": 3, "low": 4}
    assert by_tier == expected, f"Expected {expected}, got {by_tier}"

    print("PASS")