from datetime import datetime
from typing import Any, Dict

from fastapi import Depends, FastAPI
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_registry_scan_lanes(session: Session = Depends(get_session)) -> Dict[str, Any]:
    server_id = McpServerRegistry.server_id
    verdict_server_id = case(
        (McpServerRegistry.verdict.is_not(None), server_id),
        else_=None,
    )
    risk_tier_server_id = case(
        (McpServerRegistry.risk_tier.is_not(None), server_id),
        else_=None,
    )

    statement = (
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count(distinct(server_id)).label("servers"),
            func.count(distinct(verdict_server_id)).label("with_verdict"),
            func.count(distinct(risk_tier_server_id)).label("with_risk_tier"),
            func.coalesce(
                func.avg(func.coalesce(McpServerRegistry.confidence, 0.0)),
                0.0,
            ).label("avg_confidence"),
            func.min(McpServerRegistry.first_seen).label("oldest_first_seen"),
            func.max(McpServerRegistry.last_seen).label("newest_last_seen"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    )

    lanes = [
        {
            "registry_source": row["registry_source"],
            "servers": int(row["servers"] or 0),
            "with_verdict": int(row["with_verdict"] or 0),
            "with_risk_tier": int(row["with_risk_tier"] or 0),
            "avg_confidence": float(row["avg_confidence"] or 0.0),
            "oldest_first_seen": row["oldest_first_seen"],
            "newest_last_seen": row["newest_last_seen"],
        }
        for row in session.execute(statement).mappings()
    ]

    return {
        "lanes": lanes,
        "totals": {
            "servers": sum(lane["servers"] for lane in lanes),
            "sources": len(lanes),
        },
    }


get_registry_scan_lane_summary = get_registry_scan_lanes
get_scan_lane_summary = get_registry_scan_lanes
get_scan_lanes = get_registry_scan_lanes


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    seeds = [
        {
            "server_id": "server-a",
            "registry_source": "github",
            "name": "Server A",
            "confidence": 0.8,
            "verdict": "trusted",
            "risk_tier": "low",
            "first_seen": datetime(2024, 1, 1, 12, 0, 0),
            "last_seen": datetime(2024, 1, 3, 12, 0, 0),
        },
        {
            "server_id": "server-b",
            "registry_source": "github",
            "name": "Server B",
            "confidence": 0.6,
            "verdict": None,
            "risk_tier": "medium",
            "first_seen": datetime(2024, 1, 2, 12, 0, 0),
            "last_seen": datetime(2024, 1, 4, 12, 0, 0),
        },
        {
            "server_id": "server-c",
            "registry_source": "npm",
            "name": "Server C",
            "confidence": 0.9,
            "verdict": "trusted",
            "risk_tier": None,
            "first_seen": datetime(2024, 1, 5, 12, 0, 0),
            "last_seen": datetime(2024, 1, 6, 12, 0, 0),
        },
    ]
    testing_session.add_all(McpServerRegistry(**seed) for seed in seeds)
    testing_session.commit()

    def override_get_session() -> Session:
        return testing_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/scan-lanes")
    def scan_lanes(session: Session = Depends(get_session)) -> Dict[str, Any]:
        return get_registry_scan_lanes(session)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-lanes")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["lanes"]) == 2
        assert payload["totals"]["servers"] == len(seeds)

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()