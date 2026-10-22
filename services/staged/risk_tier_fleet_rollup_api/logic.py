from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _get_fleet_rollup(db: Session) -> Dict[str, Any]:
    scored_total = (
        db.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.risk_tier.is_not(None))
        ).scalar()
        or 0
    )

    tier_rows = db.execute(
        select(
            McpServerRegistry.risk_tier.label("tier"),
            func.count().label("count"),
            func.count(func.distinct(McpServerRegistry.registry_source)).label(
                "source_count"
            ),
        )
        .where(McpServerRegistry.risk_tier.is_not(None))
        .group_by(McpServerRegistry.risk_tier)
        .order_by(McpServerRegistry.risk_tier)
    ).all()

    by_tier: List[Dict[str, Any]] = []
    for row in tier_rows:
        count = int(row.count or 0)
        by_tier.append(
            {
                "tier": row.tier,
                "count": count,
                "pct": round((count / scored_total) * 100, 2)
                if scored_total
                else 0.0,
            }
        )

    source_rows = db.execute(
        select(
            McpServerRegistry.registry_source.label("source"),
            func.count().label("count"),
        )
        .where(McpServerRegistry.registry_source.is_not(None))
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    ).all()

    by_source = [
        {"source": row.source, "count": int(row.count or 0)}
        for row in source_rows
    ]

    unscored_count = int(
        db.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.risk_tier.is_(None))
        ).scalar()
        or 0
    )

    return {
        "total": int(scored_total),
        "by_tier": by_tier,
        "by_source": by_source,
        "unscored_count": unscored_count,
    }


def get_fleet_rollup(db: Session = Depends(get_session)) -> Dict[str, Any]:
    return _get_fleet_rollup(db)


def get_risk_tier_fleet_rollup(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _get_fleet_rollup(db)


def compute_fleet_rollup(db: Session) -> Dict[str, Any]:
    return _get_fleet_rollup(db)


def fleet_rollup(db: Session = Depends(get_session)) -> Dict[str, Any]:
    return _get_fleet_rollup(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/risk/fleet-rollup")
    def fleet_rollup_route(db: Session = Depends(get_session)):
        return get_fleet_rollup(db)

    seed_session = TestingSessionLocal()
    try:
        seed_session.add_all(
            [
                McpServerRegistry(
                    server_id="fleet-rollup-1",
                    risk_tier="TRUSTED_GENERAL",
                    registry_source="registry-a",
                    trust_score=95,
                ),
                McpServerRegistry(
                    server_id="fleet-rollup-2",
                    risk_tier="CAUTION_LIMITED",
                    registry_source="registry-b",
                    trust_score=65,
                ),
                McpServerRegistry(
                    server_id="fleet-rollup-3",
                    risk_tier="HIGH_RISK_ISOLATED",
                    registry_source="registry-c",
                    trust_score=20,
                ),
                McpServerRegistry(
                    server_id="fleet-rollup-4",
                    risk_tier=None,
                    registry_source="registry-d",
                    trust_score=None,
                ),
            ]
        )
        seed_session.commit()
    finally:
        seed_session.close()

    response = TestClient(test_app).get("/api/risk/fleet-rollup")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3, payload
    assert payload["unscored_count"] == 1, payload

    tier_counts = {item["tier"]: item["count"] for item in payload["by_tier"]}
    assert tier_counts["TRUSTED_GENERAL"] == 1, payload
    assert tier_counts["CAUTION_LIMITED"] == 1, payload
    assert tier_counts["HIGH_RISK_ISOLATED"] == 1, payload
    assert sum(item["count"] for item in payload["by_tier"]) == payload["total"], payload

    print("PASS")