"""HTTP surface for the watchlist tier service."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import list_watchlist_tiers
except ImportError:
    list_watchlist_tiers = None


router = APIRouter(prefix="/api", tags=["watchlist"])


class WatchlistServer(BaseModel):
    server_id: str
    name: str | None = None
    url: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    verdict: str | None = None
    confidence: float | None = None
    last_assessed: datetime | None = None
    registry_source: str | None = None


class WatchlistTiersResponse(BaseModel):
    total: int
    tier: str | None = None
    servers: list[WatchlistServer]


def _watchlist_tiers(
    db: Session,
    tier: str | None,
    limit: int,
    offset: int,
    q: str | None,
) -> tuple[int, list[WatchlistServer]]:
    conditions = []

    if tier is not None:
        conditions.append(McpServerRegistry.risk_tier == tier)

    if q is not None:
        conditions.append(
            func.lower(McpServerRegistry.name).contains(q.lower())
        )

    total = db.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(*conditions)
    )

    rows = db.scalars(
        select(McpServerRegistry)
        .where(*conditions)
        .order_by(
            desc(McpServerRegistry.last_assessed).nullslast(),
            asc(McpServerRegistry.name),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    servers = [
        WatchlistServer(
            server_id=str(row.server_id),
            name=row.name,
            url=row.url,
            risk_tier=row.risk_tier,
            trust_score=row.trust_score,
            verdict=row.verdict,
            confidence=row.confidence,
            last_assessed=row.last_assessed,
            registry_source=row.registry_source,
        )
        for row in rows
    ]

    return int(total or 0), servers


@router.get("/watchlist/tiers", response_model=WatchlistTiersResponse)
def get_watchlist_tiers(
    tier: str | None = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    q: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> WatchlistTiersResponse:
    clamped_limit = max(1, min(limit, 200))
    clamped_offset = max(0, offset)

    total, servers = _watchlist_tiers(
        db=db,
        tier=tier,
        limit=clamped_limit,
        offset=clamped_offset,
        q=q,
    )

    return WatchlistTiersResponse(total=total, tier=tier, servers=servers)


if __name__ == "__main__":
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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="wl-1",
                    name="Alpha",
                    url="https://alpha.example",
                    risk_tier="high",
                    trust_score=10.0,
                    verdict="malicious",
                    confidence=0.9,
                    last_assessed=datetime(2024, 1, 1),
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="wl-2",
                    name="Bravo",
                    url="https://bravo.example",
                    risk_tier="medium",
                    trust_score=50.0,
                    verdict="suspicious",
                    confidence=0.8,
                    last_assessed=datetime(2024, 1, 3),
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="wl-3",
                    name="Charlie",
                    url="https://charlie.example",
                    risk_tier="low",
                    trust_score=90.0,
                    verdict="trusted",
                    confidence=0.7,
                    last_assessed=datetime(2024, 1, 2),
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="wl-4",
                    name="Delta",
                    url="https://delta.example",
                    risk_tier="high",
                    trust_score=20.0,
                    verdict="malicious",
                    confidence=0.6,
                    last_assessed=None,
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="wl-5",
                    name="Echo",
                    url="https://echo.example",
                    risk_tier="medium",
                    trust_score=60.0,
                    verdict="unknown",
                    confidence=0.5,
                    last_assessed=datetime(2024, 1, 4),
                    registry_source="test",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: SessionLocal()

    with TestClient(that_app) as client:
        response = client.get("/api/watchlist/tiers")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert len(payload["servers"]) == 5
        assert payload["servers"][0]["server_id"] == "wl-5"
        assert payload["servers"][-1]["server_id"] == "wl-4"

        response = client.get("/api/watchlist/tiers", params={"tier": "high"})
        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert len(response.json()["servers"]) == 2

        response = client.get(
            "/api/watchlist/tiers", params={"tier": "not-a-tier"}
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert response.json()["servers"] == []

        response = client.get("/api/watchlist/tiers", params={"limit": 999})
        assert response.status_code == 200
        assert len(response.json()["servers"]) <= 200

    print("PASS")