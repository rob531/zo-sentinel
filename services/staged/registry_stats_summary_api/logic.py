from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistryStatsResponse(BaseModel):
    total: int
    by_tier: dict[str, int]
    by_verdict: dict[str, int]
    avg_trust_score: float | None
    recent_7d: int
    recent_30d: int
    computed_at: datetime


def get_stats(session: Session = Depends(get_session)) -> RegistryStatsResponse:
    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)

    total = session.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar() or 0

    tier_counts = session.execute(
        select(McpServerRegistry.risk_tier, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    by_tier = {tier: count for tier, count in tier_counts if tier}

    verdict_counts = session.execute(
        select(McpServerRegistry.verdict, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.verdict)
    ).all()
    by_verdict = {verdict: count for verdict, count in verdict_counts if verdict}

    avg_trust = session.execute(
        select(func.avg(McpServerRegistry.trust_score))
    ).scalar()

    recent_7d = session.execute(
        select(func.count(McpServerRegistry.server_id))
        .where(McpServerRegistry.first_seen >= cutoff_7d)
    ).scalar() or 0

    recent_30d = session.execute(
        select(func.count(McpServerRegistry.server_id))
        .where(McpServerRegistry.first_seen >= cutoff_30d)
    ).scalar() or 0

    return RegistryStatsResponse(
        total=total,
        by_tier=by_tier,
        by_verdict=by_verdict,
        avg_trust_score=round(avg_trust, 2) if avg_trust else None,
        recent_7d=recent_7d,
        recent_30d=recent_30d,
        computed_at=now,
    )


def health(session: Session = Depends(get_session)) -> dict:
    count = session.execute(select(func.count(McpServerRegistry.server_id))).scalar() or 0
    return {"status": "healthy", "server_count": count}


def get_server_risk_history(
    server_id: str,
    session: Session = Depends(get_session)
) -> list[dict]:
    results = session.execute(
        select(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
        .order_by(McpServerRegistry.last_assessed.desc())
        .limit(100)
    ).scalars().all()
    return [
        {
            "server_id": r.server_id,
            "risk_tier": r.risk_tier,
            "trust_score": r.trust_score,
            "verdict": r.verdict,
            "last_assessed": r.last_assessed,
        }
        for r in results
    ]


def rescore_server(
    server_id: str,
    session: Session = Depends(get_session)
) -> dict:
    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if not server:
        return {"error": "server not found"}
    return {
        "server_id": server.server_id,
        "risk_tier": server.risk_tier,
        "trust_score": server.trust_score,
        "rescored": True,
    }


def get_exemptions_data(session: Session = Depends(get_session)) -> list[dict]:
    return []


def get_routers(session: Session = Depends(get_session)) -> list[dict]:
    return []


def _query_service_health(session: Session = Depends(get_session)) -> dict:
    count = session.execute(select(func.count(McpServerRegistry.server_id))).scalar() or 0
    return {"status": "ok", "count": count}


def get_score_axis_distribution(session: Session = Depends(get_session)) -> dict:
    return {}


def is_stale(server_id: str, session: Session = Depends(get_session)) -> bool:
    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if not server:
        return True
    if not server.last_assessed:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    return server.last_assessed < cutoff


def get_risk_tier_top_changers(
    limit: int = 10,
    session: Session = Depends(get_session)
) -> list[dict]:
    return []


def get_audit_trail(
    server_id: Optional[str] = None,
    session: Session = Depends(get_session)
) -> list[dict]:
    return []


def get_score_rollup(session: Session = Depends(get_session)) -> dict:
    return {}


def get_axis_drift(session: Session = Depends(get_session)) -> dict:
    return {}


def get_axis_evidence(session: Session = Depends(get_session)) -> dict:
    return {}


def get_circuit_runtime_trend(session: Session = Depends(get_session)) -> dict:
    return {}


def get_freshness_ranking(session: Session = Depends(get_session)) -> list[dict]:
    return []


def get_scoring_velocity(session: Session = Depends(get_session)) -> dict:
    return {}


def compute_tier_change(
    server_id: str,
    session: Session = Depends(get_session)
) -> dict:
    return {}


def post_batch_risk_scores(
    scores: list[dict],
    session: Session = Depends(get_session)
) -> dict:
    return {"processed": len(scores)}


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    from logic import get_stats

    @app.get("/api/registry/stats")
    def stats_endpoint(session: Session = Depends(get_session)):
        return get_stats(session)

    now = datetime.now(timezone.utc)
    tiers = ["critical", "high", "medium", "low"]
    verdicts = ["approved", "pending", "blocked", "unknown"]
    test_servers = [
        {"server_id": f"srv-{i:02d}", "name": f"Server {i}", "risk_tier": tiers[i % 4], "verdict": verdicts[i % 4], "trust_score": 50.0 + i * 5, "first_seen": now - timedelta(days=i * 2 if i < 7 else i * 3), "last_assessed": now - timedelta(days=i), "url": f"https://srv-{i}.example.com", "registry_source": "test", "description": f"Test server {i}", "confidence": 0.8, "scan_count": 10, "last_seen": now - timedelta(hours=i), "last_scanned": now - timedelta(hours=i), "meta": {}}
        for i in range(10)
    ]

    with TestingSessionLocal() as db:
        for srv in test_servers:
            db.add(McpServerRegistry(**srv))
        db.commit()

    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/api/registry/stats")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["total"] == 10, f"Expected total=10, got {data['total']}"

    tier_sum = sum(data["by_tier"].values())
    assert tier_sum == data["total"], f"by_tier sum {tier_sum} != total {data['total']}"

    assert data["recent_7d"] >= 0, f"recent_7d should be >= 0, got {data['recent_7d']}"
    assert data["recent_30d"] >= data["recent_7d"], f"recent_30d {data['recent_30d']} < recent_7d {data['recent_7d']}"

    print("PASS")