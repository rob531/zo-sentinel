"""Data and computation layer for registry verdict distribution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


VALID_VERDICTS = (
    "TRUSTED_GENERAL",
    "TRUSTED_SPECIALIZED",
    "UNKNOWN",
    "SUSPICIOUS",
    "MALICIOUS",
    "HIGH_RISK_ISOLATED",
)


def get_verdict_distribution(db: Session) -> Dict[str, Any]:
    """Return registry verdict, risk-tier, and never-assessed distributions."""
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.last_assessed,
            McpServerRegistry.registry_source,
        )
    ).all()

    valid_verdicts = set(VALID_VERDICTS)
    by_verdict = {verdict: 0 for verdict in VALID_VERDICTS}
    by_risk_tier: Dict[str, int] = {}
    unknown_verdicts = 0
    stale_never_assessed = 0

    for server_id, verdict, risk_tier, trust_score, last_assessed, registry_source in rows:
        if verdict in valid_verdicts:
            by_verdict[verdict] += 1
        else:
            unknown_verdicts += 1

        tier_key = risk_tier or "unknown"
        by_risk_tier[tier_key] = by_risk_tier.get(tier_key, 0) + 1

        if last_assessed is None:
            stale_never_assessed += 1

    return {
        "total": len(rows),
        "by_verdict": by_verdict,
        "unknown_verdicts": unknown_verdicts,
        "by_risk_tier": by_risk_tier,
        "stale_never_assessed": stale_never_assessed,
    }


def get_registry_verdict_distribution(db: Session) -> Dict[str, Any]:
    """Compatibility name for the registry verdict distribution."""
    return get_verdict_distribution(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(that_engine)
    that_session_factory = sessionmaker(bind=that_engine, expire_on_commit=False)

    def override_get_session() -> Session:
        with that_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/verdict-distribution")
    def verdict_distribution_endpoint(db: Session = Depends(get_session)) -> Dict[str, Any]:
        return get_verdict_distribution(db)

    with that_session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="trusted-general",
                    verdict="TRUSTED_GENERAL",
                    risk_tier="LOW_RISK",
                    trust_score=0.95,
                    last_assessed=datetime.now(timezone.utc),
                    registry_source="contract",
                ),
                McpServerRegistry(
                    server_id="high-risk-isolated",
                    verdict="HIGH_RISK_ISOLATED",
                    risk_tier="HIGH_RISK",
                    trust_score=0.25,
                    last_assessed=datetime.now(timezone.utc),
                    registry_source="contract",
                ),
                McpServerRegistry(
                    server_id="unknown-verdict",
                    verdict=None,
                    risk_tier=None,
                    trust_score=None,
                    last_assessed=None,
                    registry_source="contract",
                ),
            ]
        )
        session.commit()

    with TestClient(that_app) as client:
        response = client.get("/api/registry/verdict-distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["by_verdict"]["TRUSTED_GENERAL"] == 1
        assert payload["by_verdict"]["HIGH_RISK_ISOLATED"] == 1
        assert payload["unknown_verdicts"] == 1
        assert payload["by_risk_tier"]["LOW_RISK"] == 1
        assert payload["by_risk_tier"]["HIGH_RISK"] == 1
        assert payload["stale_never_assessed"] == 1

    print("PASS")