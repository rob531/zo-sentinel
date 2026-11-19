"""logic.py -- registry source scoring and tiering ratios."""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_registry_source_scored_ratio(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return scoring and tiering coverage grouped by registry source."""
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
        )
    ).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    for registry_source, verdict, risk_tier, trust_score in rows:
        source = registry_source or "unknown"
        if source not in grouped:
            grouped[source] = {
                "total_servers": 0,
                "scored_servers": 0,
                "tiered_servers": 0,
                "trust_scores": [],
            }

        entry = grouped[source]
        entry["total_servers"] += 1
        if verdict is not None:
            entry["scored_servers"] += 1
            if trust_score is not None:
                entry["trust_scores"].append(float(trust_score))
        if risk_tier is not None:
            entry["tiered_servers"] += 1

    sources = []
    for registry_source in sorted(grouped):
        entry = grouped[registry_source]
        total = entry["total_servers"]
        scores = entry.pop("trust_scores")
        sources.append(
            {
                "registry_source": registry_source,
                "total_servers": total,
                "scored_servers": entry["scored_servers"],
                "tiered_servers": entry["tiered_servers"],
                "scored_ratio": round(entry["scored_servers"] / total, 4) if total else 0.0,
                "tiered_ratio": round(entry["tiered_servers"] / total, 4) if total else 0.0,
                "median_trust_score": round(float(median(scores)), 4) if scores else None,
            }
        )

    return {
        "sources": sources,
        "totals": {
            "servers": sum(item["total_servers"] for item in sources),
            "scored": sum(item["scored_servers"] for item in sources),
            "tiered": sum(item["tiered_servers"] for item in sources),
        },
    }


def get_registry_source_scored_ratio_data(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Compatibility alias for callers using the data-layer naming."""
    return get_registry_source_scored_ratio(db)


def _self_test() -> None:
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
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(bind=engine)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="source-a-1",
                    registry_source="source-a",
                    verdict="safe",
                    risk_tier="low",
                    trust_score=0.8,
                ),
                McpServerRegistry(
                    server_id="source-a-2",
                    registry_source="source-a",
                    verdict="review",
                    trust_score=0.6,
                ),
                McpServerRegistry(
                    server_id="source-a-3",
                    registry_source="source-a",
                ),
                McpServerRegistry(
                    server_id="source-b-1",
                    registry_source="source-b",
                    risk_tier="high",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/registry/source-scored-ratio")
    def source_scored_ratio(db: Session = Depends(get_session)):
        return get_registry_source_scored_ratio(db)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get(
        "/api/registry/source-scored-ratio",
        params={"registry_source": "source-a"},
    )
    assert response.status_code == 200
    payload = response.json()
    source_a = next(
        item for item in payload["sources"] if item["registry_source"] == "source-a"
    )
    assert source_a["total_servers"] == 3
    assert abs(source_a["scored_ratio"] - (2 / 3)) <= 1e-4
    assert payload["totals"]["servers"] == 4
    print("PASS")


if __name__ == "__main__":
    _self_test()