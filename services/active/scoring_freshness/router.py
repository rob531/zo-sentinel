# deps: fastapi, pydantic, sqlalchemy
"""Scoring Freshness Service.

Reports how stale each server's most recent LLM axis scores are.
Reads from `mcp_llm_axis_scores` via the app.db session.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_freshness"])


# --- Pydantic models -------------------------------------------------------

class ServerFreshness(BaseModel):
    server_id: str = Field(..., description="Server identifier")
    last_scored: datetime = Field(..., description="Timestamp of the most recent score")
    age_seconds: int = Field(..., description="Age of the most recent score in seconds")
    age_category: str = Field(
        ...,
        description=(
            "FRESH (<1h), STALE_1H (<4h), "
            "STALE_4H (<24h), STALE_24H (>=24h)"
        ),
    )


class FreshnessResponse(BaseModel):
    servers: list[ServerFreshness] = Field(..., description="List of server freshness entries")


# --- Helper ---------------------------------------------------------------

def _categorize(seconds: int) -> str:
    if seconds < 3600:
        return "FRESH"
    if seconds < 4 * 3600:
        return "STALE_1H"
    if seconds < 24 * 3600:
        return "STALE_4H"
    return "STALE_24H"


# --- Endpoint -------------------------------------------------------------

@router.get(
    "/scoring/freshness",
    response_model=FreshnessResponse,
    summary="Get scoring freshness per server",
)
def scoring_freshness(
    server_id: Optional[str] = Query(None, description="Filter to a single server"),
    db: Session = Depends(get_session),
) -> FreshnessResponse:
    """
    Return the most recent `scored_at` timestamp per server (or a single server
    when `server_id` is supplied) together with its age and freshness category.
    """
    subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    stmt = select(subq.c.server_id, subq.c.last_scored)
    if server_id is not None:
        stmt = stmt.where(subq.c.server_id == server_id)

    rows = db.execute(stmt).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No scoring data found")

    now = datetime.utcnow()
    servers: list[ServerFreshness] = []
    for srv_id, last_scored in rows:
        age_seconds = max(0, int((now - last_scored).total_seconds()))
        servers.append(
            ServerFreshness(
                server_id=srv_id,
                last_scored=last_scored,
                age_seconds=age_seconds,
                age_category=_categorize(age_seconds),
            )
        )

    return FreshnessResponse(servers=servers)


# --- Self-test ------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    from app.models import Base
    Base.metadata.create_all(test_engine)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.utcnow()

    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(server_id="s1", name="Server One"))
        sess.add(McpServerRegistry(server_id="s2", name="Server Two"))
        sess.add(McpServerRegistry(server_id="s3", name="Server Three"))
        sess.add(
            McpLlmAxisScore(
                server_id="s1",
                axis_name="overall_risk",
                model_version="v1",
                scored_at=now,
            )
        )
        sess.add(
            McpLlmAxisScore(
                server_id="s2",
                axis_name="overall_risk",
                model_version="v1",
                scored_at=now,
            )
        )
        sess.add(
            McpLlmAxisScore(
                server_id="s3",
                axis_name="overall_risk",
                model_version="v1",
                scored_at=now,
            )
        )
        sess.commit()

    client = TestClient(test_app)

    # Test: full list
    resp = client.get("/api/scoring/freshness")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if "servers" not in data:
        print("FAIL: missing 'servers' key")
        sys.exit(1)
    if len(data["servers"]) != 3:
        print(f"FAIL: expected 3 servers, got {len(data['servers'])}")
        sys.exit(1)
    for entry in data["servers"]:
        if entry["age_category"] != "FRESH":
            print(f"FAIL: expected FRESH for all servers at t=0, got {entry['age_category']}")
            sys.exit(1)

    # Test: single server filter (happy path)
    resp2 = client.get("/api/scoring/freshness?server_id=s1")
    if resp2.status_code != 200:
        print(f"FAIL: single-server filter returned {resp2.status_code}")
        sys.exit(1)
    if len(resp2.json()["servers"]) != 1:
        print("FAIL: single-server filter should return 1 result")
        sys.exit(1)

    # Test: unknown server returns 404
    resp3 = client.get("/api/scoring/freshness?server_id=nonexistent")
    if resp3.status_code != 404:
        print(f"FAIL: unknown server should 404, got {resp3.status_code}")
        sys.exit(1)

    print("PASS")
