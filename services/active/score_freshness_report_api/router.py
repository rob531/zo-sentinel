# deps: fastapi, pydantic, sqlalchemy
"""
Score Freshness Report API.

Reports staleness of each server's most recent LLM axis scores.
Reads from `mcp_llm_axis_scores` + `mcp_server_registry` via the app.db session.
Prefix /api  ->  endpoint  /api/scores/freshness
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_freshness_report_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class ServerFreshnessItem(BaseModel):
    server_id: str = Field(..., description="Server identifier")
    name: str = Field(..., description="Server display name")
    last_scored_at: datetime = Field(..., description="Most recent scored_at for this server")
    hours_since_score: float = Field(..., ge=0.0, description="Hours elapsed since last score")
    tier: Optional[str] = Field(None, description="Current risk tier")
    decision_rule_version: Optional[str] = Field(None, description="Rule version of the last score")


class FreshnessReport(BaseModel):
    servers: list[ServerFreshnessItem] = Field(
        ...,
        description="List of server freshness entries, ordered by staleness (most stale first)",
    )
    stale_count: int = Field(..., ge=0, description="Count of servers whose score age exceeds threshold_hours")
    healthy_count: int = Field(..., ge=0, description="Count of servers within the threshold")
    total_count: int = Field(..., ge=0, description="Total servers in the report")
    generated_at: datetime = Field(..., description="UTC timestamp when the report was generated")
    threshold_hours: int = Field(..., ge=1, description="Staleness threshold used for this report")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/scores/freshness",
    response_model=FreshnessReport,
    summary="Score freshness report",
)
def get_score_freshness_report(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    threshold_hours: Annotated[int, Query(ge=1)] = 48,
    server_id: Annotated[Optional[str], Query(description="Filter to a single server")] = None,
    db: Session = Depends(get_session),
) -> FreshnessReport:
    """
    Return a freshness report for server scoring data.

    - `limit` caps the number of server rows returned (most-stale first).
    - `threshold_hours` defines the staleness boundary used for counts.
    - `server_id` filters to a single server (returns 404 if not found).
    """
    now = datetime.now(timezone.utc)

    # Subquery: latest scored_at per server
    latest_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Main query: join server + latest score row
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            latest_subq.c.last_scored_at,
        )
        .join(latest_subq, McpServerRegistry.server_id == latest_subq.c.server_id)
    )

    if server_id is not None:
        stmt = stmt.where(McpServerRegistry.server_id == server_id)

    stmt = stmt.order_by(latest_subq.c.last_scored_at.asc()).limit(limit)

    rows = db.execute(stmt).all()

    if server_id is not None and not rows:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found or has no scoring data")

    # Fetch decision_rule_version from the actual latest score row per server
    tier_rows: list[tuple] = []
    if rows:
        server_ids = [r.server_id for r in rows]
        version_stmt = (
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.decision_rule_version,
                McpLlmAxisScore.scored_at,
            )
            .where(McpLlmAxisScore.server_id.in_(server_ids))
        )
        version_rows = db.execute(version_stmt).all()

        # Map server_id -> (decision_rule_version, scored_at)
        version_map: dict[str, tuple[str, datetime]] = {}
        for row in version_rows:
            sid = row.server_id
            if sid not in version_map:
                version_map[sid] = (row.decision_rule_version, row.scored_at)
            else:
                _, existing_scored_at = version_map[sid]
                if row.scored_at > existing_scored_at:
                    version_map[sid] = (row.decision_rule_version, row.scored_at)

        tier_rows = [(sid, version_map.get(sid, (None, None))[0]) for sid in server_ids]

    tier_map = {sid: ver for sid, ver in tier_rows} if tier_rows else {}

    servers: list[ServerFreshnessItem] = []
    stale_count = 0
    healthy_count = 0

    for row in rows:
        last_scored_at = row.last_scored_at
        if last_scored_at.tzinfo is None:
            last_scored_at = last_scored_at.replace(tzinfo=timezone.utc)

        delta = now - last_scored_at
        hours_since_score = max(0.0, delta.total_seconds() / 3600.0)

        decision_rule_version = tier_map.get(row.server_id)

        servers.append(
            ServerFreshnessItem(
                server_id=row.server_id,
                name=row.name or row.server_id,
                last_scored_at=last_scored_at,
                hours_since_score=round(hours_since_score, 2),
                tier=row.risk_tier,
                decision_rule_version=decision_rule_version,
            )
        )

        if hours_since_score >= threshold_hours:
            stale_count += 1
        else:
            healthy_count += 1

    total_count = len(servers)

    return FreshnessReport(
        servers=servers,
        stale_count=stale_count,
        healthy_count=healthy_count,
        total_count=total_count,
        generated_at=now,
        threshold_hours=threshold_hours,
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
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(test_engine)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.now(timezone.utc)

    # Seed data
    with TestSessionLocal() as sess:
        # Fresh server: scored 2 hours ago
        sess.add(
            McpServerRegistry(
                server_id="srv-fresh",
                name="Fresh Server",
                risk_tier="low",
                registry_source="test",
                url="https://test.example",
            )
        )
        sess.add(
            McpLlmAxisScore(
                id=1,
                server_id="srv-fresh",
                axis_name="overall_risk",
                label="low",
                label_index=0,
                model_version="v1",
                decision_rule_version="r1.0",
                adapter_sha256="deadbeef",
                scored_at=datetime.fromisoformat(now.isoformat().replace("+00:00", "")) - __import__("datetime").timedelta(hours=2),
            )
        )

        # Stale server: scored 72 hours ago
        sess.add(
            McpServerRegistry(
                server_id="srv-stale",
                name="Stale Server",
                risk_tier="high",
                registry_source="test",
                url="https://stale.example",
            )
        )
        sess.add(
            McpLlmAxisScore(
                id=2,
                server_id="srv-stale",
                axis_name="overall_risk",
                label="high",
                label_index=3,
                model_version="v1",
                decision_rule_version="r1.0",
                adapter_sha256="cafebabe",
                scored_at=datetime.fromisoformat(now.isoformat().replace("+00:00", "")) - __import__("datetime").timedelta(hours=72),
            )
        )
        sess.commit()

    client = TestClient(test_app)

    # --- Happy path: report with 48h threshold ---
    resp = client.get("/api/scores/freshness?threshold_hours=48&limit=10")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()

    if "servers" not in data:
        print("FAIL: missing 'servers' key")
        sys.exit(1)
    if data["total_count"] != 2:
        print(f"FAIL: expected 2 servers, got {data['total_count']}")
        sys.exit(1)
    if data["stale_count"] != 1:
        print(f"FAIL: expected stale_count=1, got {data['stale_count']}")
        sys.exit(1)
    if data["healthy_count"] != 1:
        print(f"FAIL: expected healthy_count=1, got {data['healthy_count']}")
        sys.exit(1)
    # Most stale first
    if data["servers"][0]["server_id"] != "srv-stale":
        print(f"FAIL: expected srv-stale first, got {data['servers'][0]['server_id']}")
        sys.exit(1)
    # Fresh server is second
    if data["servers"][1]["server_id"] != "srv-fresh":
        print(f"FAIL: expected srv-fresh second, got {data['servers'][1]['server_id']}")
        sys.exit(1)
    print("  PASS /scores/freshness (report)")

    # --- Single server filter (happy path) ---
    resp2 = client.get("/api/scores/freshness?server_id=srv-fresh")
    if resp2.status_code != 200:
        print(f"FAIL: single server filter returned {resp2.status_code}")
        sys.exit(1)
    if resp2.json()["total_count"] != 1:
        print("FAIL: single server filter should return 1 server")
        sys.exit(1)
    if resp2.json()["servers"][0]["server_id"] != "srv-fresh":
        print("FAIL: single server filter returned wrong server")
        sys.exit(1)
    print("  PASS /scores/freshness (single server filter)")

    # --- Unknown server returns 404 ---
    resp3 = client.get("/api/scores/freshness?server_id=no-such-server")
    if resp3.status_code != 404:
        print(f"FAIL: unknown server should 404, got {resp3.status_code}")
        sys.exit(1)
    print("  PASS /scores/freshness (unknown server -> 404)")

    # --- Auth failure: no session (403-like behaviour via dependency) ---
    # Note: since this is a public endpoint (auth="public" in directive),
    # we don't test auth failure here. The happy path + 404 case above
    # cover the contract.

    print("\nPASS")
