import json
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

# Real data layer imports
from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class ServerInfo(BaseModel):
    server_id: str
    name: str
    url: str
    verdict: str
    risk_tier: str
    trust_score: float
    confidence: float
    last_assessed: str
    stale: bool


class WatchlistResponse(BaseModel):
    requested: int
    found: int
    missing: List[str] = Field(default_factory=list)
    servers: List[ServerInfo] = Field(default_factory=list)


@router.get("/watchlist/servers", response_model=WatchlistResponse)
def get_servers(
    ids: str = Query(..., description="Comma‑separated server IDs"),
    tier: Optional[str] = Query(None, description="Risk tier filter"),
    db: Session = Depends(get_session),
):
    requested_ids = [i.strip() for i in ids.split(",") if i.strip()]
    if not requested_ids:
        raise HTTPException(status_code=400, detail="No IDs supplied")

    stmt = select(McpServerRegistry).where(McpServerRegistry.server_id.in_(requested_ids))
    rows = db.execute(stmt).scalars().all()
    row_map = {r.server_id: r for r in rows}

    servers: List[ServerInfo] = []
    missing: List[str] = []

    now = datetime.utcnow()
    for sid in requested_ids:
        rec = row_map.get(sid)
        if not rec:
            missing.append(sid)
            continue

        if tier and rec.risk_tier != tier:
            continue

        last_assessed = rec.last_assessed or now
        stale = (now - last_assessed) > timedelta(days=7)

        servers.append(
            ServerInfo(
                server_id=rec.server_id,
                name=rec.name,
                url=rec.url,
                verdict=rec.verdict,
                risk_tier=rec.risk_tier,
                trust_score=rec.trust_score,
                confidence=rec.confidence,
                last_assessed=last_assessed.isoformat(),
                stale=stale,
            )
        )

    found_count = len(servers)
    return WatchlistResponse(
        requested=len(requested_ids),
        found=found_count,
        missing=missing,
        servers=servers,
    )


# --------------------------------------------------------------------------- #
# Self‑test when run as a module
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine compatible with the app's Base
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Override the dependency used by the router
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build a temporary FastAPI app for the test
    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    # Create tables and seed data
    Base.metadata.create_all(bind=engine)
    with TestingSessionLocal() as db:
        now = datetime.utcnow()
        old = now - timedelta(days=10)

        rows = [
            McpServerRegistry(
                server_id="a",
                name="Fresh Server",
                url="http://fresh.example",
                verdict="clean",
                risk_tier="HIGH_RISK_ISOLATED",
                trust_score=0.9,
                confidence=0.95,
                last_assessed=now,
                last_seen=now,
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="b",
                name="Stale Server",
                url="http://stale.example",
                verdict="clean",
                risk_tier="HIGH_RISK_ISOLATED",
                trust_score=0.8,
                confidence=0.85,
                last_assessed=old,
                last_seen=old,
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="c",
                name="Other Server",
                url="http://other.example",
                verdict="clean",
                risk_tier="LOW_RISK",
                trust_score=0.7,
                confidence=0.75,
                last_assessed=now,
                last_seen=now,
                registry_source="test",
            ),
        ]
        db.add_all(rows)
        db.commit()

    client = TestClient(app)
    resp = client.get(
        "/api/watchlist/servers",
        params={"ids": "a,b,zzz", "tier": "HIGH_RISK_ISOLATED"},
    )
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    try:
        assert data["requested"] == 3
        assert data["found"] == 2
        assert set(data["missing"]) == {"zzz"}
        # verify stale flags
        stale_map = {s["server_id"]: s["stale"] for s in data["servers"]}
        assert stale_map["a"] is False
        assert stale_map["b"] is True
    except AssertionError:
        print("FAIL: response validation failed", file=sys.stderr)
        print(json.dumps(data, indent=2), file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)