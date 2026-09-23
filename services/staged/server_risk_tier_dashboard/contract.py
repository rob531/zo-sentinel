# services/staged/server_risk_tier_dashboard/contract.py
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from typing import List, Generator
from sqlalchemy.orm import Session
from sqlalchemy import func, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports (must not be changed)
from app.db import get_session, Base
from app.models import McpServerRegistry

router = APIRouter(prefix="/dashboard")


class TierDistribution(BaseModel):
    tier: str
    count: int


class DashboardResponse(BaseModel):
    total_servers: int
    tier_distribution: List[TierDistribution]


@router.get("/server-risk-tiers", response_model=DashboardResponse)
def get_server_risk_tiers(session: Session = Depends(get_session)):
    total = session.query(func.count()).select_from(McpServerRegistry).scalar() or 0
    rows = (
        session.query(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    distribution = [
        TierDistribution(tier=tier if tier is not None else "unknown", count=count)
        for tier, count in rows
    ]
    return DashboardResponse(total_servers=total, tier_distribution=distribution)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.server_risk_tier_dashboard.contract`)
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    # Build a minimal FastAPI app with the router
    app = FastAPI()
    app.include_router(router)

    # --------------------------------------------------------------------- #
    # In‑memory SQLite session (overrides the real app DB)
    # --------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def get_test_session() -> Generator[Session, None, None]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Apply the override
    app.dependency_overrides[get_session] = get_test_session

    # Create tables for the real models
    Base.metadata.create_all(bind=test_engine)

    # --------------------------------------------------------------------- #
    # Seed test data: 5 servers with varying risk tiers
    # --------------------------------------------------------------------- #
    test_servers = [
        McpServerRegistry(
            server_id="srv-1",
            name="server‑one",
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="server‑two",
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="server‑three",
            risk_tier="high",
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="server‑four",
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="srv-5",
            name="server‑five",
            risk_tier="critical",
        ),
    ]

    # Insert seed data
    with TestSessionLocal() as sess:
        sess.add_all(test_servers)
        sess.commit()

    # --------------------------------------------------------------------- #
    # Execute request against the endpoint
    # --------------------------------------------------------------------- #
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/dashboard/server-risk-tiers")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()

    # Expected counts
    expected_total = 5
    expected_counts = {"low": 2, "medium": 1, "high": 1, "critical": 1}

    assert payload["total_servers"] == expected_total, "Total server count mismatch"

    # Convert list of dicts to mapping for easy comparison
    received_counts = {item["tier"]: item["count"] for item in payload["tier_distribution"]}

    assert received_counts == expected_counts, f"Tier distribution mismatch: {received_counts}"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()