"""
services.staged.overview_metrics.contract
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Real application imports – must not be mocked or re‑defined.
from app.db import get_session, Base
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


@router.get("/overview/metrics")
def get_overview_metrics(db: Session = Depends(get_session)):
    """Return aggregated overview metrics for all registered servers."""
    now = datetime.utcnow()

    # total servers
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # by risk tier
    tier_rows = (
        db.query(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    by_tier = {tier: count for tier, count in tier_rows if tier is not None}

    # by verdict
    verdict_rows = (
        db.query(McpServerRegistry.verdict, func.count())
        .group_by(McpServerRegistry.verdict)
        .all()
    )
    by_verdict = {verdict: count for verdict, count in verdict_rows if verdict is not None}

    # growth windows
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)

    last_7d = (
        db.query(func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.first_seen >= cutoff_7d)
        .scalar()
        or 0
    )
    last_30d = (
        db.query(func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.first_seen >= cutoff_30d)
        .scalar()
        or 0
    )

    return {
        "total": total,
        "by_tier": by_tier,
        "by_verdict": by_verdict,
        "growth": {"last_7d": last_7d, "last_30d": last_30d},
        "as_of": now.isoformat() + "Z",
    }


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.overview_metrics.contract)   #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    # Build a minimal FastAPI app that includes this router.
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    # --------------------------------------------------------------------- #
    # Override the real DB session with an in‑memory SQLite DB for testing. #
    # --------------------------------------------------------------------- #
    TEST_ENGINE = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=TEST_ENGINE
    )

    # Create tables matching the real models.
    Base.metadata.create_all(bind=TEST_ENGINE)

    # Dependency override.
    def override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # --------------------------------------------------------------------- #
    # Seed the test database with deterministic data.                      #
    # --------------------------------------------------------------------- #
    now = datetime.utcnow()
    seeds = [
        McpServerRegistry(
            server_id="srv-1",
            name="server‑one",
            risk_tier="high",
            verdict="malicious",
            first_seen=now - timedelta(days=1),
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="server‑two",
            risk_tier="medium",
            verdict="benign",
            first_seen=now - timedelta(days=2),
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="server‑three",
            risk_tier="low",
            verdict="benign",
            first_seen=now - timedelta(days=10),
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="server‑four",
            risk_tier="high",
            verdict="suspicious",
            first_seen=now - timedelta(days=20),
        ),
        McpServerRegistry(
            server_id="srv-5",
            name="server‑five",
            risk_tier="medium",
            verdict="malicious",
            first_seen=now - timedelta(days=40),
        ),
    ]

    with TestSessionLocal() as db:
        db.add_all(seeds)
        db.commit()

    # --------------------------------------------------------------------- #
    # Execute the request against the test client and validate expectations.#
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/overview/metrics")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["total"] == 5, "total count mismatch"
    assert isinstance(data["by_tier"], dict) and data["by_tier"], "by_tier empty"
    assert isinstance(data["by_verdict"], dict) and data["by_verdict"], "by_verdict empty"
    assert data["growth"]["last_7d"] >= 2, "growth.last_7d too low"

    print("PASS")
    sys.exit(0)