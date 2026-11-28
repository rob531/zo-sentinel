from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef


class HotspotServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None


class ThreatActorHotspot(BaseModel):
    actor_id: int
    actor_label: str
    pulse_name: str
    indicator_type: str
    server_count: int
    worst_risk_tier: Optional[str] = None
    sample_servers: List[HotspotServer]


class ThreatActorHotspotResponse(BaseModel):
    days: int
    generated_at: datetime
    total_actors: int
    hotspots: List[ThreatActorHotspot]


router = APIRouter(prefix="/api")

_RISK_TIER_RANK = {
    "critical": 6,
    "high": 5,
    "elevated": 5,
    "medium": 4,
    "moderate": 4,
    "low": 3,
    "minimal": 2,
    "info": 1,
    "unknown": 0,
    "none": 0,
}


def _risk_rank(value: Optional[str]) -> int:
    if not value:
        return 0
    return _RISK_TIER_RANK.get(value.strip().lower(), 0)


def _hotspot_query() -> str:
    registry_table = McpServerRegistry.__tablename__
    intel_table = ThreatIntelRef.__tablename__
    return f"""
        SELECT
            a.threat_id AS actor_id,
            a.server_id,
            s.name AS server_name,
            s.risk_tier,
            t.indicator_type,
            t.indicator_value,
            t.pulse_name,
            t.pulse_created,
            t.source,
            t.source_url,
            t.is_aggregator
        FROM mcp_threat_associations a
        LEFT JOIN {registry_table} s
            ON s.server_id = a.server_id
        LEFT JOIN {intel_table} t
            ON t.id = a.threat_id
        WHERE t.pulse_created >= :cutoff
        ORDER BY a.threat_id, a.server_id
    """


@router.get(
    "/threat/actors/hotspots",
    response_model=ThreatActorHotspotResponse,
)
def get_threat_actor_hotspots(
    days: int = Query(default=30, ge=1),
    limit: int = Query(default=20, ge=1),
    min_servers: int = Query(default=1, ge=1),
    session: Session = Depends(get_session),
) -> ThreatActorHotspotResponse:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    rows = session.execute(text(_hotspot_query()), {"cutoff": cutoff}).mappings().all()

    actors: dict[int, dict] = {}

    for row in rows:
        actor_id = int(row["actor_id"])
        actor = actors.setdefault(
            actor_id,
            {
                "indicator_type": row["indicator_type"] or "unknown",
                "actor_label": (
                    row["indicator_value"]
                    or row["source"]
                    or f"Threat actor {actor_id}"
                ),
                "pulse_name": (
                    row["pulse_name"]
                    or row["indicator_value"]
                    or row["source"]
                    or f"Threat actor {actor_id}"
                ),
                "servers": {},
            },
        )

        server_id = str(row["server_id"])
        actor["servers"].setdefault(
            server_id,
            {
                "server_id": server_id,
                "name": row["server_name"],
                "risk_tier": row["risk_tier"],
            },
        )

    hotspots = []
    for actor_id, actor in actors.items():
        servers = sorted(
            actor["servers"].values(),
            key=lambda server: (
                -_risk_rank(server["risk_tier"]),
                server["server_id"],
            ),
        )
        server_count = len(servers)
        if server_count < min_servers:
            continue

        worst_risk_tier = servers[0]["risk_tier"] if servers else None
        hotspots.append(
            {
                "actor_id": actor_id,
                "actor_label": actor["actor_label"],
                "pulse_name": actor["pulse_name"],
                "indicator_type": actor["indicator_type"],
                "server_count": server_count,
                "worst_risk_tier": worst_risk_tier,
                "sample_servers": servers[:5],
            }
        )

    hotspots.sort(key=lambda hotspot: (-hotspot["server_count"], hotspot["actor_id"]))
    hotspots = hotspots[:limit]

    return ThreatActorHotspotResponse(
        days=days,
        generated_at=datetime.now(timezone.utc),
        total_actors=len(actors),
        hotspots=hotspots,
    )


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT,
                    trust_score REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    server_id TEXT NOT NULL,
                    threat_id INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type TEXT,
                    indicator_value TEXT,
                    pulse_name TEXT,
                    pulse_created TIMESTAMP,
                    source TEXT,
                    source_url TEXT,
                    is_aggregator INTEGER
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, trust_score)
                VALUES
                    ('srv-1', 'Alpha Server', 'critical', 0.95),
                    ('srv-2', 'Bravo Server', 'medium', 0.75),
                    ('srv-3', 'Charlie Server', 'high', 0.85)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_threat_associations (server_id, threat_id)
                VALUES
                    ('srv-1', 101),
                    ('srv-2', 101),
                    ('srv-2', 202),
                    ('srv-3', 202)
                """
            )
        )
        pulse_created = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs
                    (id, indicator_type, indicator_value, pulse_name,
                     pulse_created, source, source_url, is_aggregator)
                VALUES
                    (101, 'actor', 'APT-Alpha', 'Alpha Pulse',
                     :pulse_created, 'fixture', 'https://example.test/alpha', 0),
                    (202, 'actor', 'APT-Bravo', 'Bravo Pulse',
                     :pulse_created, 'fixture', 'https://example.test/bravo', 0)
                """
            ),
            {"pulse_created": pulse_created},
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with Session(bind=engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/threat/actors/hotspots",
                params={"days": 30, "limit": 20, "min_servers": 1},
            )
            assert response.status_code == 200, response.text

            payload = response.json()
            assert len(payload["hotspots"]) == 2, payload
            assert payload["hotspots"][0]["server_count"] == 2, payload
            assert payload["total_actors"] == 2, payload
            for hotspot in payload["hotspots"]:
                assert hotspot["pulse_name"], payload
                assert hotspot["actor_label"], payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_self_test()