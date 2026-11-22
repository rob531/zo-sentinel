"""logic.py -- data/computation layer for app3t_registry_host_distribution_api.

Provides a FastAPI router exposing a GET endpoint that summarises how MCP
servers are distributed across registry hosts/organisations.
"""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


def _aggregate_host_distribution(
    rows: List[tuple],
) -> List[Dict]:
    """Aggregate raw rows into the required host summary structure."""
    host_map: Dict[str, Dict] = {}
    for host, tier, trust in rows:
        if host not in host_map:
            host_map[host] = {
                "host": host,
                "server_count": 0,
                "risk_tier_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 0},
                "trust_sum": 0.0,
            }
        data = host_map[host]
        data["server_count"] += 1
        if tier in ("LOW", "MEDIUM", "HIGH"):
            data["risk_tier_counts"][tier] += 1
        # trust_score may be null; treat null as 0 for averaging purposes
        data["trust_sum"] += float(trust) if trust is not None else 0.0

    # finalize avg_trust_score and drop temporary trust_sum
    for data in host_map.values():
        data["avg_trust_score"] = (
            data["trust_sum"] / data["server_count"]
            if data["server_count"]
            else 0.0
        )
        del data["trust_sum"]
    return list(host_map.values())


@router.get("/registry/hosts")
def get_registry_host_distribution(
    limit: int = Query(50, ge=1),
    min_servers: int = Query(1, ge=0),
    db: Session = Depends(get_session),
):
    """Return a summary of server distribution per registry host."""
    # total number of servers in the registry
    total_servers = db.scalar(select(func.count()).select_from(McpServerRegistry))

    # fetch needed columns for aggregation
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
        )
    ).all()

    hosts = _aggregate_host_distribution(rows)

    # filter by min_servers
    hosts = [h for h in hosts if h["server_count"] >= min_servers]

    # ordering: server_count desc, then host asc
    hosts.sort(key=lambda x: (-x["server_count"], x["host"]))

    # apply limit
    hosts = hosts[:limit]

    response = {
        "total_servers": int(total_servers or 0),
        "host_count": len(hosts),
        "hosts": hosts,
    }
    return response


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    # in‑memory SQLite engine mirroring the real schema
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # create tables
    Base.metadata.create_all(bind=engine)

    # dependency override
    def get_test_session() -> Session:
        with SessionLocal() as sess:
            yield sess

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # seed data: 4 servers across 3 hosts
    with SessionLocal() as sess:
        sess.add_all(
            [
                McpServerRegistry(
                    server_id="s1",
                    name="Server One",
                    url="https://hostA.example.com/1",
                    registry_source="hostA",
                    trust_score=0.8,
                    risk_tier="LOW",
                ),
                McpServerRegistry(
                    server_id="s2",
                    name="Server Two",
                    url="https://hostA.example.com/2",
                    registry_source="hostA",
                    trust_score=0.4,
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id="s3",
                    name="Server Three",
                    url="https://hostB.example.com/1",
                    registry_source="hostB",
                    trust_score=0.6,
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id="s4",
                    name="Server Four",
                    url="https://hostC.example.com/1",
                    registry_source="hostC",
                    trust_score=0.9,
                    risk_tier="LOW",
                ),
            ]
        )
        sess.commit()

    client = TestClient(app)
    resp = client.get("/api/registry/hosts")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    data = resp.json()

    assert data["total_servers"] == 4, "total_servers mismatch"
    assert data["host_count"] == 3, "host_count mismatch"

    # locate hostA entry
    host_a = next(h for h in data["hosts"] if h["host"] == "hostA")
    assert host_a["server_count"] == 2, "hostA server_count mismatch"
    # ordering check: hostA should be first (most servers)
    assert data["hosts"][0]["host"] == "hostA", "ordering mismatch"

    print("PASS")