from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_registry_source_scored_ratio(
    db: Session,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute registry-source scoring coverage using the real app registry."""
    query = select(
        McpServerRegistry.registry_source,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.trust_score,
    )
    if registry_source is not None:
        query = query.where(McpServerRegistry.registry_source == registry_source)

    rows = db.execute(query).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    for source, verdict, risk_tier, trust_score in rows:
        source_name = source or "unknown"
        group = grouped.setdefault(
            source_name,
            {
                "total_servers": 0,
                "scored_servers": 0,
                "tiered_servers": 0,
                "trust_scores": [],
            },
        )
        group["total_servers"] += 1

        if verdict is not None:
            group["scored_servers"] += 1
            if trust_score is not None:
                group["trust_scores"].append(float(trust_score))

        if risk_tier is not None:
            group["tiered_servers"] += 1

    sources: List[Dict[str, Any]] = []
    for source_name in sorted(grouped):
        group = grouped[source_name]
        total = group["total_servers"]
        scored = group["scored_servers"]
        tiered = group["tiered_servers"]
        trust_scores = group["trust_scores"]

        sources.append(
            {
                "registry_source": source_name,
                "total_servers": total,
                "scored_servers": scored,
                "tiered_servers": tiered,
                "scored_ratio": round(scored / total, 4) if total else 0.0,
                "tiered_ratio": round(tiered / total, 4) if total else 0.0,
                "median_trust_score": median(trust_scores) if trust_scores else None,
            }
        )

    totals = {
        "servers": sum(source["total_servers"] for source in sources),
        "scored": sum(source["scored_servers"] for source in sources),
        "tiered": sum(source["tiered_servers"] for source in sources),
    }

    return {"sources": sources, "totals": totals}


get_registry_source_scored_ratios = get_registry_source_scored_ratio
registry_source_scored_ratio = get_registry_source_scored_ratio


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="source-a-1",
                    registry_source="source-a",
                    verdict="malicious",
                    risk_tier="high",
                    trust_score=0.2,
                ),
                McpServerRegistry(
                    server_id="source-a-2",
                    registry_source="source-a",
                    verdict="safe",
                    trust_score=0.8,
                ),
                McpServerRegistry(
                    server_id="source-a-3",
                    registry_source="source-a",
                    trust_score=0.9,
                ),
                McpServerRegistry(
                    server_id="source-b-1",
                    registry_source="source-b",
                    verdict="unknown",
                    trust_score=0.5,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/registry/source-scored-ratio")
    def registry_source_scored_ratio_endpoint(
        db: Session = Depends(get_session),
    ):
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **get_registry_source_scored_ratio(db),
        }

    with TestClient(that_app) as client:
        response = client.get("/registry/source-scored-ratio")
        assert response.status_code == 200
        payload = response.json()
        busiest = max(payload["sources"], key=lambda source: source["total_servers"])
        assert busiest["total_servers"] == 3
        assert abs(busiest["scored_ratio"] - (2 / 3)) <= 1e-4
        assert payload["totals"]["servers"] == 4

    print("PASS")