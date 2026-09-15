"""High‑risk servers contract.

Provides ``GET /api/servers/high-risk`` returning a list of servers whose
``risk_tier`` is either ``HIGH_RISK_ISOLATED`` or ``KNOWN_THREAT``.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, nullslast
from sqlalchemy.orm import Session

# Real data layer – must not be replaced.
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class HighRiskServerItem(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    risk_tier: str
    last_scanned: datetime | None = None
    confidence: float | None = None


class HighRiskServersResponse(BaseModel):
    limit: int = Field(..., description="Requested limit")
    count: int = Field(..., description="Number of items returned")
    items: List[HighRiskServerItem]


@router.get(
    "/servers/high-risk",
    response_model=HighRiskServersResponse,
    summary="List high‑risk servers",
)
def get_high_risk_servers(
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description="Maximum number of servers to return (default 50, max 200)",
    ),
    session: Session = Depends(get_session),
) -> HighRiskServersResponse:
    """Return servers whose ``risk_tier`` indicates high risk."""
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_scanned,
            McpServerRegistry.confidence,
        )
        .where(
            McpServerRegistry.risk_tier.in_(
                ["HIGH_RISK_ISOLATED", "KNOWN_THREAT"]
            )
        )
        .order_by(nullslast(desc(McpServerRegistry.last_scanned)))
        .limit(limit)
    )
    rows = session.execute(stmt).all()

    items = [
        HighRiskServerItem(
            server_id=row.server_id,
            name=row.name,
            verdict=row.verdict,
            risk_tier=row.risk_tier,
            last_scanned=row.last_scanned,
            confidence=row.confidence,
        )
        for row in rows
    ]

    return HighRiskServersResponse(limit=limit, count=len(items), items=items)


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.high_risk_servers.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real ``get_session`` dependency)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables for the real model metadata
    from app.models import Base  # noqa: E402  (import after engine creation)

    Base.metadata.create_all(bind=engine)

    # Seed data
    def seed_db() -> None:
        sess = SessionLocal()
        now = datetime.utcnow()
        rows = [
            McpServerRegistry(
                server_id="srv-1",
                name="Isolated Server",
                verdict="malicious",
                risk_tier="HIGH_RISK_ISOLATED",
                last_scanned=now,
                confidence=0.95,
            ),
            McpServerRegistry(
                server_id="srv-2",
                name="Known Threat Server",
                verdict="malicious",
                risk_tier="KNOWN_THREAT",
                last_scanned=now.replace(microsecond=0),
                confidence=0.90,
            ),
            McpServerRegistry(
                server_id="srv-3",
                name="Trusted General Server",
                verdict="benign",
                risk_tier="TRUSTED_GENERAL",
                last_scanned=now,
                confidence=0.99,
            ),
            McpServerRegistry(
                server_id="srv-4",
                name="Enterprise Controlled Server",
                verdict="benign",
                risk_tier="ENTERPRISE_CONTROLLED",
                last_scanned=now,
                confidence=0.99,
            ),
        ]
        sess.add_all(rows)
        sess.commit()
        sess.close()

    seed_db()

    # Dependency override
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # --------------------------------------------------------------- #
    # Test 1 – explicit limit, expect only the two high‑risk rows
    # --------------------------------------------------------------- #
    resp = client.get("/api/servers/high-risk?limit=10")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["count"] == 2, f"Expected 2 items, got {data['count']}"
    assert len(data["items"]) == 2
    # Verify ordering by ``last_scanned`` descending
    ts0 = data["items"][0]["last_scanned"]
    ts1 = data["items"][1]["last_scanned"]
    assert ts0 >= ts1, "Items not ordered by last_scanned DESC"

    # --------------------------------------------------------------- #
    # Test 2 – default limit (no query string)
    # --------------------------------------------------------------- #
    resp2 = client.get("/api/servers/high-risk")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["count"] <= 50, "Default limit exceeded 50"

    print("PASS")