from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

app = FastAPI()


class OverviewSummaryResponse(BaseModel):
    total_servers: int
    risk_tier_counts: dict[str, int]
    source_counts: dict[str, int]
    never_scored_count: int
    avg_trust_score: float | None
    computed_at: str


RISK_TIERS = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
]


def compute_overview_summary(session: Session) -> OverviewSummaryResponse:
    computed_at = datetime.now(timezone.utc).isoformat()

    total_result = session.execute(
        select(func.count()).select_from(McpServerRegistry)
    ).scalar()
    total_servers = total_result or 0

    risk_tier_counts: dict[str, int] = {tier: 0 for tier in RISK_TIERS}
    tier_results = session.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .select_from(McpServerRegistry)
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    for tier, count in tier_results:
        if tier in risk_tier_counts:
            risk_tier_counts[tier] = count

    source_results = session.execute(
        select(McpServerRegistry.registry_source, func.count())
        .select_from(McpServerRegistry)
        .group_by(McpServerRegistry.registry_source)
    ).all()
    source_counts: dict[str, int] = {src: cnt for src, cnt in source_results}

    scored_subquery = (
        select(func.distinct(McpLlmAxisScore.server_id))
        .subquery()
    )
    scored_count = session.execute(
        select(func.count()).select_from(scored_subquery)
    ).scalar() or 0
    never_scored_count = total_servers - scored_count

    avg_result = session.execute(
        select(func.avg(McpServerRegistry.trust_score)).where(
            McpServerRegistry.trust_score.isnot(None)
        )
    ).scalar()
    avg_trust_score = round(avg_result, 2) if avg_result is not None else None

    return OverviewSummaryResponse(
        total_servers=total_servers,
        risk_tier_counts=risk_tier_counts,
        source_counts=source_counts,
        never_scored_count=never_scored_count,
        avg_trust_score=avg_trust_score,
        computed_at=computed_at,
    )


@app.get("/api/registry/overview/summary", response_model=OverviewSummaryResponse)
def get_overview_summary(session: Session = Depends(get_session)) -> OverviewSummaryResponse:
    return compute_overview_summary(session)


def _run_self_test() -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()

    @test_app.get("/api/registry/overview/summary", response_model=OverviewSummaryResponse)
    def test_endpoint(session: Session = Depends(get_session)) -> OverviewSummaryResponse:
        return compute_overview_summary(session)

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    db = TestingSessionLocal()
    try:
        db.add(McpServerRegistry(
            server_id="srv001", name="Server One", registry_source="source_a",
            risk_tier="TRUSTED_GENERAL", trust_score=85.5, url="https://srv001.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv002", name="Server Two", registry_source="source_a",
            risk_tier="TRUSTED_GENERAL", trust_score=90.0, url="https://srv002.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv003", name="Server Three", registry_source="source_a",
            risk_tier="TRUSTED_GENERAL", trust_score=88.0, url="https://srv003.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv004", name="Server Four", registry_source="source_a",
            risk_tier="ENTERPRISE_CONTROLLED", trust_score=75.0, url="https://srv004.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv005", name="Server Five", registry_source="source_a",
            risk_tier="ENTERPRISE_CONTROLLED", trust_score=72.5, url="https://srv005.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv006", name="Server Six", registry_source="source_b",
            risk_tier="ENTERPRISE_CONTROLLED", trust_score=70.0, url="https://srv006.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv007", name="Server Seven", registry_source="source_b",
            risk_tier="HIGH_RISK_ISOLATED", trust_score=20.0, url="https://srv007.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv008", name="Server Eight", registry_source="source_b",
            risk_tier="HIGH_RISK_ISOLATED", trust_score=15.0, url="https://srv008.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv009", name="Server Nine", registry_source="source_b",
            risk_tier="HIGH_RISK_ISOLATED", trust_score=18.0, url="https://srv009.example"
        ))
        db.add(McpServerRegistry(
            server_id="srv010", name="Server Ten", registry_source="source_b",
            risk_tier="HIGH_RISK_ISOLATED", trust_score=22.0, url="https://srv010.example"
        ))
        db.commit()
    finally:
        db.close()

    response = client.get("/api/registry/overview/summary")
    if response.status_code != 200:
        print(f"FAIL: status {response.status_code}")
        return 1

    data = response.json()
    if data["total_servers"] != 10:
        print(f"FAIL: total_servers {data['total_servers']} != 10")
        return 1

    present_tiers = [t for t, c in data["risk_tier_counts"].items() if c > 0]
    if len(present_tiers) != 3:
        print(f"FAIL: expected 3 tiers with data, got {len(present_tiers)}: {present_tiers}")
        return 1

    try:
        datetime.fromisoformat(data["computed_at"].replace("Z", "+00:00"))
    except ValueError:
        print(f"FAIL: computed_at not ISO8601: {data['computed_at']}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    exit(_run_self_test())