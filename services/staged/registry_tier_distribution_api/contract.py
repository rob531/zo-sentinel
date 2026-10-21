from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select, case, literal
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


app = FastAPI()


class ByRiskTier(BaseModel):
    risk_tier: str
    count: int
    share: float


class ByVerdict(BaseModel):
    verdict: str
    count: int


class BySource(BaseModel):
    registry_source: str
    count: int


class TopRisky(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    trust_score: Optional[float]
    last_assessed: Optional[str]


class TierDistributionResponse(BaseModel):
    total: int
    by_risk_tier: list[ByRiskTier]
    by_verdict: list[ByVerdict]
    by_source: list[BySource]
    top_risky: list[TopRisky]
    stale_count: int


@app.get("/api/registry/tiers", response_model=TierDistributionResponse)
def get_registry_tiers(
    source: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_session),
):
    base_filter = []
    if source:
        base_filter.append(McpServerRegistry.registry_source == source)
    if q:
        base_filter.append(func.lower(McpServerRegistry.name).contains(q.lower()))

    total = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(*base_filter)
    ).scalar() or 0

    risk_tier_expr = case(
        (McpServerRegistry.risk_tier.is_(None), "UNSET"),
        else_=McpServerRegistry.risk_tier,
    )
    verdict_expr = case(
        (McpServerRegistry.verdict.is_(None), "UNSET"),
        else_=McpServerRegistry.verdict,
    )

    risk_tier_rows = db.execute(
        select(
            risk_tier_expr.label("risk_tier"),
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .where(*base_filter)
        .group_by(risk_tier_expr)
    ).all()

    by_risk_tier = [
        ByRiskTier(
            risk_tier=str(rt.risk_tier),
            count=rt.count,
            share=round(rt.count / total, 4) if total > 0 else 0.0,
        )
        for rt in risk_tier_rows
    ]

    verdict_rows = db.execute(
        select(
            verdict_expr.label("verdict"),
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .where(*base_filter)
        .group_by(verdict_expr)
    ).all()

    by_verdict = [
        ByVerdict(verdict=str(v.verdict), count=v.count) for v in verdict_rows
    ]

    source_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .where(*base_filter)
        .group_by(McpServerRegistry.registry_source)
    ).all()

    by_source = [
        BySource(registry_source=str(s.registry_source), count=s.count)
        for s in source_rows
    ]

    top_risky_rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.last_assessed,
        )
        .where(*base_filter)
        .order_by(McpServerRegistry.trust_score.asc().nullsfirst())
        .limit(limit)
    ).all()

    top_risky = [
        TopRisky(
            server_id=str(tr.server_id),
            name=tr.name,
            risk_tier=tr.risk_tier,
            trust_score=tr.trust_score,
            last_assessed=tr.last_assessed.isoformat() if tr.last_assessed else None,
        )
        for tr in top_risky_rows
    ]

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    stale_count = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            *base_filter,
            McpServerRegistry.last_assessed < cutoff,
        )
    ).scalar() or 0

    return TierDistributionResponse(
        total=total,
        by_risk_tier=by_risk_tier,
        by_verdict=by_verdict,
        by_source=by_source,
        top_risky=top_risky,
        stale_count=stale_count,
    )


def seed_db(session: Session) -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=45)
    recent = now - timedelta(days=5)

    servers = [
        McpServerRegistry(
            server_id="srv-001",
            name="high-risk-server-a",
            registry_source="community",
            trust_score=0.15,
            verdict="MALICIOUS",
            risk_tier="HIGH_RISK_ISOLATED",
            confidence=0.92,
            last_assessed=recent,
            last_seen=recent,
            first_seen=old,
            scan_count=12,
        ),
        McpServerRegistry(
            server_id="srv-002",
            name="high-risk-server-b",
            registry_source="verified",
            trust_score=0.22,
            verdict="SUSPICIOUS",
            risk_tier="HIGH_RISK_ISOLATED",
            confidence=0.88,
            last_assessed=recent,
            last_seen=recent,
            first_seen=old,
            scan_count=8,
        ),
        McpServerRegistry(
            server_id="srv-003",
            name="trusted-server-a",
            registry_source="community",
            trust_score=0.91,
            verdict="TRUSTED",
            risk_tier="TRUSTED_GENERAL",
            confidence=0.95,
            last_assessed=recent,
            last_seen=recent,
            first_seen=old,
            scan_count=45,
        ),
        McpServerRegistry(
            server_id="srv-004",
            name="trusted-server-b",
            registry_source="verified",
            trust_score=0.88,
            verdict="TRUSTED",
            risk_tier="TRUSTED_GENERAL",
            confidence=0.93,
            last_assessed=recent,
            last_seen=recent,
            first_seen=old,
            scan_count=32,
        ),
        McpServerRegistry(
            server_id="srv-005",
            name="unset-tier-server",
            registry_source="community",
            trust_score=0.55,
            verdict="UNKNOWN",
            risk_tier=None,
            confidence=0.50,
            last_assessed=recent,
            last_seen=recent,
            first_seen=old,
            scan_count=5,
        ),
        McpServerRegistry(
            server_id="srv-006",
            name="stale-server",
            registry_source="community",
            trust_score=0.40,
            verdict="SUSPICIOUS",
            risk_tier="REQUIRES_REVIEW",
            confidence=0.65,
            last_assessed=old,
            last_seen=old,
            first_seen=old,
            scan_count=3,
        ),
    ]

    for srv in servers:
        session.add(srv)
    session.commit()


def run_self_test() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base

    Base.metadata.create_all(engine)

    from app.db import get_session as orig_get_session

    def _override_get_session():
        return Session(bind=engine)

    test_app = FastAPI()
    test_app.include_router(app.router)

    with Session(bind=engine) as session:
        seed_db(session)

    test_app.dependency_overrides[orig_get_session] = _override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(test_app)

    response = client.get("/api/registry/tiers")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()

    assert data["total"] == 6, f"Expected total=6, got {data['total']}"

    unset_bucket = next(
        (b for b in data["by_risk_tier"] if b["risk_tier"] == "UNSET"), None
    )
    assert unset_bucket is not None, "Missing UNSET bucket in by_risk_tier"
    assert unset_bucket["count"] == 1, f"Expected UNSET count=1, got {unset_bucket['count']}"

    assert data["stale_count"] >= 1, f"Expected stale_count>=1, got {data['stale_count']}"

    for entry in data["top_risky"]:
        assert "server_id" in entry, "top_risky entry missing server_id"
        assert entry["server_id"], "top_risky entry has empty server_id"

    print("PASS")


if __name__ == "__main__":
    run_self_test()