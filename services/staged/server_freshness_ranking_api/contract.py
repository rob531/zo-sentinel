# services/staged/server_freshness_ranking_api/contract.py
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry, Base
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["server_freshness_ranking"])


DEFAULT_SLA_DAYS = 7


class ServerFreshness(BaseModel):
    server_id: str
    name: str
    risk_tier: str | None = None
    last_assessed_iso: datetime = Field(..., alias="last_assessed")
    scan_count: int | None = None
    staleness_days: int

    class Config:
        orm_mode = True
        allow_population_by_field_name = True


class FreshnessRankingResponse(BaseModel):
    servers: List[ServerFreshness]
    total_stale: int
    limit: int
    fetched_at: datetime


@router.get(
    "/servers/freshness",
    response_model=FreshnessRankingResponse,
    status_code=status.HTTP_200_OK,
)
def get_server_freshness_ranking(
    limit: int = Query(10, ge=1),
    session: Session = Depends(get_session),
):
    now = datetime.utcnow()

    # total stale count across the whole table
    total_stale = (
        session.query(McpServerRegistry)
        .filter(
            McpServerRegistry.last_assessed
            < now - timedelta(days=DEFAULT_SLA_DAYS)
        )
        .count()
    )

    # fetch the oldest‑first servers limited by `limit`
    rows = (
        session.query(McpServerRegistry)
        .order_by(McpServerRegistry.last_assessed.asc())
        .limit(limit)
        .all()
    )

    servers: List[ServerFreshness] = []
    for row in rows:
        if not row.last_assessed:
            continue
        staleness = (now - row.last_assessed).days
        servers.append(
            ServerFreshness(
                server_id=row.server_id,
                name=row.name,
                risk_tier=row.risk_tier,
                last_assessed=row.last_assessed,
                scan_count=row.scan_count,
                staleness_days=staleness,
            )
        )

    return FreshnessRankingResponse(
        servers=servers,
        total_stale=total_stale,
        limit=limit,
        fetched_at=now,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # In‑memory SQLite engine mirroring the real models
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override
    def get_test_session() -> Session:
        with TestSession() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    now = datetime.utcnow()
    stale_dates = [now - timedelta(days=10), now - timedelta(days=15)]
    fresh_dates = [
        now - timedelta(days=1),
        now - timedelta(days=3),
        now - timedelta(days=5),
    ]
    all_dates = stale_dates + fresh_dates

    with TestSession() as s:
        for i, dt in enumerate(all_dates, start=1):
            s.add(
                McpServerRegistry(
                    server_id=f"svr{i}",
                    name=f"Server {i}",
                    risk_tier="high" if i % 2 else "low",
                    last_assessed=dt,
                    scan_count=i * 10,
                )
            )
        s.commit()

    client = TestClient(app)

    resp = client.get("/api/servers/freshness?limit=5")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}")
        sys.exit(1)

    data = resp.json()
    servers = data.get("servers", [])
    if len(servers) != 5:
        print(f"FAIL: expected 5 servers, got {len(servers)}")
        sys.exit(1)

    # Verify ordering by staleness descending (oldest first)
    stalenesses = [s["staleness_days"] for s in servers]
    if stalenesses != sorted(stalenesses, reverse=True):
        print("FAIL: servers not ordered by staleness descending")
        sys.exit(1)

    if data.get("total_stale") != 2:
        print(f"FAIL: expected total_stale 2, got {data.get('total_stale')}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)