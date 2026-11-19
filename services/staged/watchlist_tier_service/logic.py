from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class WatchlistServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    last_assessed: Optional[datetime] = None
    registry_source: Optional[str] = None


class WatchlistTiersResponse(BaseModel):
    total: int
    tier: Optional[str] = None
    servers: List[WatchlistServer]


def get_watchlist_tiers(
    db: Session,
    tier: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
) -> Dict[str, Any]:
    """Return registry servers matching the requested tier and name filter."""
    filters = []

    if tier is not None:
        filters.append(McpServerRegistry.risk_tier == tier)

    if q is not None:
        escaped_q = (
            q.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        filters.append(
            McpServerRegistry.name.ilike(f"%{escaped_q}%", escape="\\")
        )

    total = int(
        db.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(*filters)
        ).scalar_one()
    )

    clamped_limit = max(1, min(200, int(limit)))
    clamped_offset = max(0, int(offset))

    rows = db.execute(
        select(McpServerRegistry)
        .where(*filters)
        .order_by(
            McpServerRegistry.last_assessed.desc().nullslast(),
            McpServerRegistry.name.asc(),
            McpServerRegistry.server_id.asc(),
        )
        .offset(clamped_offset)
        .limit(clamped_limit)
    ).scalars().all()

    servers = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "url": row.url,
            "risk_tier": row.risk_tier,
            "trust_score": row.trust_score,
            "verdict": row.verdict,
            "confidence": row.confidence,
            "last_assessed": row.last_assessed,
            "registry_source": row.registry_source,
        }
        for row in rows
    ]

    return {
        "total": total,
        "tier": tier,
        "servers": servers,
    }


def get_db(db: Session = Depends(get_session)) -> Session:
    return db


list_watchlist_tiers = get_watchlist_tiers
fetch_watchlist_tiers = get_watchlist_tiers
list_tiers = get_watchlist_tiers
get_tiers = get_watchlist_tiers


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_db = TestSession()

    seeded = [
        McpServerRegistry(
            server_id="srv-critical-old",
            name="Critical Old",
            url="https://critical-old.example",
            risk_tier="critical",
            trust_score=20,
            verdict="unsafe",
            confidence=0.9,
            last_assessed=datetime(2024, 1, 1, tzinfo=timezone.utc),
            registry_source="registry",
        ),
        McpServerRegistry(
            server_id="srv-critical-new",
            name="Critical New",
            url="https://critical-new.example",
            risk_tier="critical",
            trust_score=30,
            verdict="unsafe",
            confidence=0.95,
            last_assessed=datetime(2024, 3, 1, tzinfo=timezone.utc),
            registry_source="registry",
        ),
        McpServerRegistry(
            server_id="srv-medium",
            name="Medium Server",
            url="https://medium.example",
            risk_tier="medium",
            trust_score=55,
            verdict="review",
            confidence=0.8,
            last_assessed=datetime(2024, 2, 1, tzinfo=timezone.utc),
            registry_source="registry",
        ),
        McpServerRegistry(
            server_id="srv-high",
            name="High Server",
            url="https://high.example",
            risk_tier="high",
            trust_score=75,
            verdict="safe",
            confidence=0.85,
            last_assessed=datetime(2024, 4, 1, tzinfo=timezone.utc),
            registry_source="registry",
        ),
        McpServerRegistry(
            server_id="srv-low",
            name="Low Server",
            url="https://low.example",
            risk_tier="low",
            trust_score=90,
            verdict="safe",
            confidence=0.99,
            last_assessed=None,
            registry_source="registry",
        ),
    ]
    test_db.add_all(seeded)
    test_db.commit()

    test_app = FastAPI()

    @test_app.get("/api/watchlist/tiers")
    def test_watchlist_tiers(
        session: Session = Depends(get_session),
        tier: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        q: Optional[str] = None,
    ):
        return get_watchlist_tiers(
            db=session,
            tier=tier,
            limit=limit,
            offset=offset,
            q=q,
        )

    test_app.dependency_overrides[get_session] = lambda: test_db

    with TestClient(test_app) as client:
        response = client.get("/api/watchlist/tiers")
        assert response.status_code == 200
        assert response.json()["total"] == 5

        response = client.get(
            "/api/watchlist/tiers",
            params={"tier": "critical", "limit": 1},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert body["tier"] == "critical"
        assert len(body["servers"]) == 1

        response = client.get(
            "/api/watchlist/tiers",
            params={"tier": "unknown", "limit": 500},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["servers"] == []
        assert len(body["servers"]) <= 200

        response = client.get("/api/watchlist/tiers")
        body = response.json()
        assert [
            server["server_id"] for server in body["servers"]
        ] == [
            "srv-high",
            "srv-critical-new",
            "srv-medium",
            "srv-critical-old",
            "srv-low",
        ]

        response = client.get("/api/watchlist/tiers", params={"q": "MEDIUM"})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["servers"][0]["server_id"] == "srv-medium"

    test_db.close()
    test_engine.dispose()
    print("PASS")