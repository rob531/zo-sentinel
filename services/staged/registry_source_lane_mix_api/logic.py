from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def get_source_lane_mix(db: Session, days: int = 30) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=days)

    total_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count().label("total"),
        )
        .group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    ).all()

    fresh_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count().label("fresh"),
        )
        .where(
            McpServerRegistry.last_seen.is_not(None),
            McpServerRegistry.last_seen >= cutoff,
        )
        .group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    ).all()

    fresh_counts = {
        (source, tier): int(count)
        for source, tier, count in fresh_rows
    }
    lanes: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "by_tier": {}, "fresh": 0, "stale": 0}
    )

    for source, tier, count in total_rows:
        source_name = str(source) if source is not None else "unknown"
        tier_name = str(tier) if tier is not None else "unknown"
        count = int(count)
        lane = lanes[source_name]
        lane["total"] += count
        lane["by_tier"][tier_name] = lane["by_tier"].get(tier_name, 0) + count
        lane["fresh"] += fresh_counts.get((source, tier), 0)

    for lane in lanes.values():
        lane["stale"] = lane["total"] - lane["fresh"]
        lane["by_tier"] = dict(sorted(lane["by_tier"].items()))

    return {
        "days": days,
        "generated_at": generated_at.isoformat(),
        "lanes": [
            {"registry_source": source, **lanes[source]}
            for source in sorted(lanes)
        ],
    }


def get_registry_source_lane_mix(db: Session, days: int = 30) -> dict[str, Any]:
    return get_source_lane_mix(db=db, days=days)


if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestingSessionLocal = __import__(
        "sqlalchemy.orm", fromlist=["sessionmaker"]
    ).sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="lane-github-1",
                    name="Lane GitHub One",
                    url="https://example.test/github/one",
                    registry_source="github",
                    risk_tier="critical",
                    first_seen=now - timedelta(days=10),
                    last_seen=now - timedelta(hours=1),
                    last_assessed=now,
                    last_scanned=now,
                    trust_score=0.95,
                ),
                McpServerRegistry(
                    server_id="lane-github-2",
                    name="Lane GitHub Two",
                    url="https://example.test/github/two",
                    registry_source="github",
                    risk_tier="high",
                    first_seen=now - timedelta(days=60),
                    last_seen=now - timedelta(days=45),
                    last_assessed=now,
                    last_scanned=now,
                    trust_score=0.75,
                ),
                McpServerRegistry(
                    server_id="lane-gitlab-1",
                    name="Lane GitLab One",
                    url="https://example.test/gitlab/one",
                    registry_source="gitlab",
                    risk_tier="medium",
                    first_seen=now - timedelta(days=5),
                    last_seen=now - timedelta(hours=2),
                    last_assessed=now,
                    last_scanned=now,
                    trust_score=0.85,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()

    @that_app.get("/api/registry/source-lane-mix")
    def source_lane_mix(days: int = 30, db: Session = Depends(get_session)):
        return get_source_lane_mix(db=db, days=days)

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/source-lane-mix", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 30
        assert "generated_at" in payload
        assert len(payload["lanes"]) == 2

        lanes = {lane["registry_source"]: lane for lane in payload["lanes"]}
        assert lanes["github"]["total"] == 2
        assert set(lanes["github"]["by_tier"]) == {"critical", "high"}
        assert lanes["github"]["fresh"] == 1
        assert lanes["github"]["stale"] == 1
        assert lanes["gitlab"]["total"] == 1
        assert set(lanes["gitlab"]["by_tier"]) == {"medium"}
        assert lanes["gitlab"]["fresh"] == 1
        assert lanes["gitlab"]["stale"] == 0

    print("PASS")