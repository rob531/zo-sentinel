# deps: fastapi, pydantic, sqlalchemy
"""server_scoring_recency_api -- routers.

GET /api/scoring/recency  Returns recency / staleness summary for scored servers.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_recency_data

router = APIRouter(prefix="/api", tags=["server_scoring_recency_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class StaleServer(BaseModel):
    server_id: str = Field(..., description="Server identifier")
    name: Optional[str] = Field(None, description="Server display name")
    risk_tier: Optional[str] = Field(None, description="Current risk tier")
    last_scored: Optional[datetime] = Field(
        None, description="UTC timestamp of the most recent score"
    )
    days_ago: Optional[int] = Field(
        None, description="Days since last score (None if never scored)"
    )

    model_config = Field(default={}, description="Pydantic v2 config dict")


class RecencyResponse(BaseModel):
    total_servers: int = Field(..., description="Servers that have ever been scored")
    stale_count: int = Field(..., description="Servers not scored within the threshold")
    freshness_pct: float = Field(..., description="Percentage of fresh (non-stale) servers")
    stale_servers: list[StaleServer] = Field(
        ..., description="Details of each stale server"
    )


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/scoring/recency",
    response_model=RecencyResponse,
    summary="Get scoring recency / staleness for scored servers",
)
def get_scoring_recency(
    days: int = Query(default=7, ge=1, le=365, description="Staleness threshold in days"),
    db: Session = Depends(get_session),
) -> RecencyResponse:
    """
    Returns a recency summary for all servers that appear in mcp_llm_axis_scores:

    - **total_servers**: count of servers that have at least one score.
    - **stale_count**: count of servers whose most recent score is older than *days*.
    - **freshness_pct**: percentage of servers that are NOT stale.
    - **stale_servers**: per-server details for stale entries.
    """
    raw = get_recency_data(days=days, db=db)
    return RecencyResponse(
        total_servers=raw["total_servers"],
        stale_count=raw["stale_count"],
        freshness_pct=raw["freshness_pct"],
        stale_servers=[
            StaleServer(
                server_id=s["server_id"],
                name=s["name"],
                risk_tier=s["risk_tier"],
                last_scored=s["last_scored"],
                days_ago=s["days_ago"],
            )
            for s in raw["stale_servers"]
        ],
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # Seed: 4 servers.  s1=stale (10d), s2=fresh (2d), s3=stale (8d), s4=fresh (1d)
    now = datetime.now(timezone.utc)
    with TestSessionLocal() as sess:
        for sid, name, tier in [
            ("s1", "Stale Server One",   "HIGH"),
            ("s2", "Fresh Server Two",    "MEDIUM"),
            ("s3", "Stale Server Three",  "LOW"),
            ("s4", "Fresh Server Four",   "CRITICAL"),
        ]:
            sess.add(__import__("app.models", fromlist=["McpServerRegistry"]).McpServerRegistry(
                server_id=sid, name=name, risk_tier=tier,
            ))

        from app.models import McpLlmAxisScore
        scored_at_map = {
            "s1": now - __import__("datetime", fromlist=["timedelta"]).timedelta(days=10),
            "s2": now - __import__("datetime", fromlist=["timedelta"]).timedelta(days=2),
            "s3": now - __import__("datetime", fromlist=["timedelta"]).timedelta(days=8),
            "s4": now - __import__("datetime", fromlist=["timedelta"]).timedelta(days=1),
        }
        for sid, scored_at in scored_at_map.items():
            sess.add(McpLlmAxisScore(
                server_id=sid,
                axis_name="overall_risk",
                model_version="v1",
                label="MEDIUM",
                scored_at=scored_at,
            ))
        sess.commit()

    client = TestClient(app)

    # Test 1: happy path, days=7 -> stale_count must be 2
    resp = client.get("/api/scoring/recency", params={"days": 7})
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    if data.get("total_servers") != 4:
        print(f"FAIL: total_servers expected 4, got {data.get('total_servers')}", file=sys.stderr)
        sys.exit(1)
    if data.get("stale_count") != 2:
        print(f"FAIL: stale_count expected 2, got {data.get('stale_count')}", file=sys.stderr)
        sys.exit(1)
    for s in data.get("stale_servers", []):
        if not isinstance(s.get("days_ago"), int) or s["days_ago"] <= 7:
            print(f"FAIL: stale server days_ago must be integer > 7, got {s}", file=sys.stderr)
            sys.exit(1)

    # Test 2: days=14 -> only s1 (10d) and s3 (8d) stale? No, 14d threshold means
    #         s3 (8d) is fresh, only s1 (10d) is stale → stale_count=1
    resp2 = client.get("/api/scoring/recency", params={"days": 14})
    if resp2.status_code != 200:
        print(f"FAIL: days=14 returned {resp2.status_code}", file=sys.stderr)
        sys.exit(1)
    data2 = resp2.json()
    if data2.get("stale_count") != 1:
        print(f"FAIL: stale_count for days=14 expected 1, got {data2.get('stale_count')}", file=sys.stderr)
        sys.exit(1)
    if data2["stale_servers"][0]["server_id"] != "s1":
        print(f"FAIL: expected s1 to be stale for days=14, got {data2['stale_servers']}", file=sys.stderr)
        sys.exit(1)

    # Test 3: days=0 is validation error (ge=1)
    resp3 = client.get("/api/scoring/recency", params={"days": 0})
    if resp3.status_code != 422:
        print(f"FAIL: days=0 should return 422, got {resp3.status_code}", file=sys.stderr)
        sys.exit(1)

    # Test 4: days > 365 is validation error (le=365)
    resp4 = client.get("/api/scoring/recency", params={"days": 400})
    if resp4.status_code != 422:
        print(f"FAIL: days=400 should return 422, got {resp4.status_code}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
