"""Logic for the verdict distribution histogram service."""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_verdict_distribution(db: Session) -> Dict[str, object]:
    """Compute verdict, risk-tier, and per-source distribution counts."""
    rows = db.execute(
        select(
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
        )
    ).all()

    verdicts: Dict[str, int] = {}
    risk_tiers: Dict[str, int] = {}
    source_verdicts: Dict[str, Dict[str, int]] = {}

    for verdict, risk_tier, registry_source in rows:
        verdict_key = verdict or "unknown"
        risk_tier_key = risk_tier or "unknown"
        source_key = registry_source or "unknown"

        verdicts[verdict_key] = verdicts.get(verdict_key, 0) + 1
        risk_tiers[risk_tier_key] = risk_tiers.get(risk_tier_key, 0) + 1
        source_verdicts.setdefault(source_key, {})
        source_verdicts[source_key][verdict_key] = (
            source_verdicts[source_key].get(verdict_key, 0) + 1
        )

    by_source: List[Dict[str, object]] = []
    for source in sorted(source_verdicts):
        counts = source_verdicts[source]
        top_verdict, top_count = sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )[0]
        by_source.append(
            {"source": source, "verdict": top_verdict, "count": int(top_count)}
        )

    return {
        "verdicts": verdicts,
        "risk_tiers": risk_tiers,
        "by_source": by_source,
        "total": len(rows),
    }


compute_verdict_distribution = get_verdict_distribution
get_verdict_histogram = get_verdict_distribution


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_session = TestSession()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="histogram-server-1",
                verdict="trusted",
                risk_tier="low",
                registry_source="registry-a",
            ),
            McpServerRegistry(
                server_id="histogram-server-2",
                verdict="trusted",
                risk_tier="low",
                registry_source="registry-a",
            ),
            McpServerRegistry(
                server_id="histogram-server-3",
                verdict="suspicious",
                risk_tier="high",
                registry_source="registry-b",
            ),
            McpServerRegistry(
                server_id="histogram-server-4",
                verdict="suspicious",
                risk_tier="high",
                registry_source="registry-b",
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/verdict-histogram")
    def histogram_endpoint(db: Session = Depends(get_session)):
        return get_verdict_distribution(db)

    test_app.dependency_overrides[get_session] = lambda: test_session
    response = TestClient(test_app).get("/api/registry/verdict-histogram")

    assert response.status_code == 200
    payload = response.json()
    assert sum(payload["verdicts"].values()) == payload["total"] == 4
    assert len(payload["verdicts"]) >= 2
    print("PASS")