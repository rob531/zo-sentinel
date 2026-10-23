from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _iso8601(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_registry_lanes(
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    statement = (
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count().label("server_count"),
            func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
            func.count(McpServerRegistry.risk_tier).label("tiered_count"),
            func.count(func.distinct(McpServerRegistry.risk_tier)).label(
                "distinct_tiers"
            ),
            func.max(McpServerRegistry.last_seen).label("last_seen"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(
            func.count().desc(),
            McpServerRegistry.registry_source.asc(),
        )
    )

    lanes: list[dict[str, Any]] = []
    for row in session.execute(statement):
        average = row.avg_trust_score
        lanes.append(
            {
                "registry_source": (
                    str(row.registry_source)
                    if row.registry_source is not None
                    else ""
                ),
                "server_count": int(row.server_count),
                "avg_trust_score": (
                    float(average) if average is not None else None
                ),
                "tiered_count": int(row.tiered_count),
                "distinct_tiers": int(row.distinct_tiers),
                "last_seen": _iso8601(row.last_seen),
            }
        )
    return lanes


get_lanes = get_registry_lanes
compute_lanes = get_registry_lanes
compute_registry_lanes = get_registry_lanes
get_lane_data = get_registry_lanes


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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    McpServerRegistry.__table__.create(test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="lane-contract-1",
                    name="Contract Server 1",
                    registry_source="registry-a",
                    risk_tier="high",
                    verdict="approved",
                    trust_score=0.40,
                    first_seen=datetime(2024, 1, 1),
                    last_seen=datetime(2024, 1, 3),
                    last_scanned=datetime(2024, 1, 3),
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="lane-contract-2",
                    name="Contract Server 2",
                    registry_source="registry-a",
                    risk_tier="medium",
                    verdict="approved",
                    trust_score=0.60,
                    first_seen=datetime(2024, 1, 1),
                    last_seen=datetime(2024, 1, 2),
                    last_scanned=datetime(2024, 1, 2),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="lane-contract-3",
                    name="Contract Server 3",
                    registry_source="registry-a",
                    risk_tier=None,
                    verdict="pending",
                    trust_score=0.80,
                    first_seen=datetime(2024, 1, 1),
                    last_seen=datetime(2024, 1, 1),
                    last_scanned=datetime(2024, 1, 1),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="lane-contract-4",
                    name="Contract Server 4",
                    registry_source="registry-b",
                    risk_tier="low",
                    verdict="approved",
                    trust_score=0.90,
                    first_seen=datetime(2024, 1, 1),
                    last_seen=datetime(2024, 1, 4),
                    last_scanned=datetime(2024, 1, 4),
                    scan_count=4,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/lanes")
    def registry_lanes(session: Session = Depends(get_session)):
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lanes": get_registry_lanes(session),
        }

    response = TestClient(test_app).get("/api/registry/lanes")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["lanes"]) == 2
    lane_a = next(
        lane
        for lane in payload["lanes"]
        if lane["registry_source"] == "registry-a"
    )
    assert lane_a["server_count"] == 3
    assert abs(lane_a["avg_trust_score"] - (0.40 + 0.60 + 0.80) / 3) < 1e-9
    print("PASS")