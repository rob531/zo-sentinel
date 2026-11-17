from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_verdict_mix_distribution(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return registry-wide risk-tier and verdict distributions."""
    total = session.scalar(select(func.count(McpServerRegistry.server_id))) or 0

    risk_rows = session.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("server_count"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .order_by(McpServerRegistry.risk_tier)
    ).all()

    verdict_rows = session.execute(
        select(
            McpServerRegistry.verdict,
            func.count(McpServerRegistry.server_id).label("server_count"),
            func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
        )
        .group_by(McpServerRegistry.verdict)
        .order_by(McpServerRegistry.verdict)
    ).all()

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)
    assessed_24h = session.scalar(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.last_assessed >= cutoff
        )
    ) or 0

    return {
        "total": int(total),
        "by_risk_tier": [
            {
                "tier": row.risk_tier if row.risk_tier is not None else "unknown",
                "count": int(row.server_count),
            }
            for row in risk_rows
        ],
        "by_verdict": [
            {
                "verdict": row.verdict if row.verdict is not None else "unknown",
                "count": int(row.server_count),
                "avg_trust_score": (
                    float(row.avg_trust_score)
                    if row.avg_trust_score is not None
                    else None
                ),
            }
            for row in verdict_rows
        ],
        "assessed_24h": int(assessed_24h),
    }


get_verdict_mix = get_verdict_mix_distribution
get_distribution = get_verdict_mix_distribution


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="trusted-1",
                name="trusted",
                risk_tier="trusted",
                trust_score=90,
                verdict="trusted",
                last_assessed=now,
            ),
            McpServerRegistry(
                server_id="elevated-1",
                name="elevated",
                risk_tier="elevated",
                trust_score=40,
                verdict="elevated",
                last_assessed=now - timedelta(days=2),
            ),
            McpServerRegistry(
                server_id="critical-1",
                name="critical",
                risk_tier="critical",
                trust_score=None,
                verdict="unscored",
                last_assessed=now,
            ),
        ]
    )
    session.commit()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/verdict-mix")
    def verdict_mix(session: Session = Depends(get_session)):
        return get_verdict_mix_distribution(session)

    response = TestClient(that_app).get("/api/verdict-mix")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total"] == 3
    assert any(
        item["tier"] == "critical" and item["count"] == 1
        for item in payload["by_risk_tier"]
    )
    assert any(
        item["verdict"] == "unscored" and item["avg_trust_score"] is None
        for item in payload["by_verdict"]
    )
    print("PASS")
    session.close()
    engine.dispose()