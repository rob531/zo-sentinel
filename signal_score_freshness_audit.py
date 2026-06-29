# signal_score_freshness_audit.py
# deps: fastapi, pydantic, sqlalchemy, PyJWT, passlib
"""
Audit signal freshness across all MCPs in mcp_server_registry.

SLA rules (PRODUCT_SPEC §4):
  1. New MCPs  – first verdict within 24 h of first_seen.
  2. Live MCPs – re-verdict within 7 days of last_assessed.

Only READS; no automatic re-assessment is triggered.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from app.security import get_principal
from app.rbac import require_role

router = APIRouter(tags=["signal-freshness"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class StaleServerReport(BaseModel):
    server_id: str
    name: str | None
    url: str | None
    risk_tier: str | None
    verdict: str | None
    first_seen: datetime | None
    last_assessed: datetime | None
    last_scored_at: datetime | None
    days_since_last_assessed: float | None
    days_since_first_seen: float | None
    stale_reason: str  # "new_mcp_no_verdict" | "live_stale_7d"
    axis_count: int = 0


class FreshnessAuditResponse(BaseModel):
    audited_at: datetime
    new_mcp_violations_24h: int
    live_stale_violations_7d: int
    total_violations: int
    total_servers_audited: int
    servers: List[StaleServerReport]


# ---------------------------------------------------------------------------
# Constants (SLA thresholds)
# ---------------------------------------------------------------------------

NEW_MCP_SLA_HOURS = 24
LIVE_MCP_SLA_DAYS = 7
NOW = datetime.now(timezone.utc)


def _days_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    aware = dt
    if aware.tzinfo is None:
        aware = aware.replace(tzinfo=timezone.utc)
    return (NOW - aware).total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/audit/signal-freshness",
    response_model=FreshnessAuditResponse,
    status_code=status.HTTP_200_OK,
    summary="Audit MCP signal freshness SLAs",
    description=(
        "Returns every MCP server that violates the 24 h first-verdict SLA "
        "(new MCPs) or the 7-day re-assessment SLA (live MCPs). "
        "Admin role required."
    ),
)
def signal_freshness_audit(
    principal=Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> FreshnessAuditResponse:
    """
    Scans mcp_server_registry joined with mcp_llm_axis_scores.

    - Violation type 1: server with no axis rows (never verdict-ed) AND
      first_seen > 24 h ago.
    - Violation type 2: server with axis rows whose MAX(scored_at) is more
      than 7 days in the past.

    This is a READ-ONLY operational report; it never triggers re-assessment.
    """
    results: List[StaleServerReport] = []

    # Sub-query: max scored_at + row count per server_id from axis scores
    latest_score_subq = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
            func.count(McpLlmAxisScore.id).label("axis_count"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Outer-join so servers with zero scores are still included
    rows = (
        db.query(
            McpServerRegistry,
            latest_score_subq.c.last_scored_at,
            latest_score_subq.c.axis_count,
        )
        .outerjoin(
            latest_score_subq,
            McpServerRegistry.server_id == latest_score_subq.c.server_id,
        )
        .all()
    )

    cutoff_new = NOW - timedelta(hours=NEW_MCP_SLA_HOURS)
    cutoff_live = NOW - timedelta(days=LIVE_MCP_SLA_DAYS)

    new_violations = 0
    live_violations = 0

    for reg, last_scored_at, axis_count in rows:
        axis_count = axis_count or 0  # NULL from outer join → 0

        # Type-1 violation: never verdict-ed and first_seen > 24 h ago
        if axis_count == 0 and reg.first_seen is not None and reg.first_seen < cutoff_new:
            results.append(
                StaleServerReport(
                    server_id=reg.server_id,
                    name=reg.name,
                    url=reg.url,
                    risk_tier=reg.risk_tier,
                    verdict=reg.verdict,
                    first_seen=reg.first_seen,
                    last_assessed=reg.last_assessed,
                    last_scored_at=None,
                    days_since_last_assessed=_days_since(reg.last_assessed),
                    days_since_first_seen=_days_since(reg.first_seen),
                    stale_reason="new_mcp_no_verdict",
                    axis_count=0,
                )
            )
            new_violations += 1
            continue

        # Type-2 violation: has verdict rows but last_scored_at > 7 days ago
        if axis_count > 0 and last_scored_at is not None and last_scored_at < cutoff_live:
            results.append(
                StaleServerReport(
                    server_id=reg.server_id,
                    name=reg.name,
                    url=reg.url,
                    risk_tier=reg.risk_tier,
                    verdict=reg.verdict,
                    first_seen=reg.first_seen,
                    last_assessed=reg.last_assessed,
                    last_scored_at=last_scored_at,
                    days_since_last_assessed=_days_since(last_scored_at),
                    days_since_first_seen=_days_since(reg.first_seen),
                    stale_reason="live_stale_7d",
                    axis_count=axis_count,
                )
            )
            live_violations += 1

    return FreshnessAuditResponse(
        audited_at=NOW,
        new_mcp_violations_24h=new_violations,
        live_stale_violations_7d=live_violations,
        total_violations=len(results),
        total_servers_audited=len(rows),
        servers=results,
    )


# ---------------------------------------------------------------------------
# Self-test (runs against SQLite in-process; no live Postgres required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import McpServerRegistry, McpLlmAxisScore
    from app.security import get_principal, Principal
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In-memory SQLite for self-test isolation
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # ---------------------------------------------------------------------------
    # Seed fixture data (SQLite needs explicit id for BigInteger PK)
    # ---------------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    past_8h = now - timedelta(hours=8)
    past_3d = now - timedelta(days=3)
    past_10d = now - timedelta(days=10)
    past_30d = now - timedelta(days=30)

    def seed(session: Session) -> None:
        # Fresh server – healthy, within both SLA windows
        session.add(McpServerRegistry(
            server_id="fresh-server",
            name="Fresh Server",
            first_seen=past_3d,
            last_assessed=past_3d,
            risk_tier="low",
        ))
        session.add(McpLlmAxisScore(
            id=1,
            server_id="fresh-server",
            axis_name="overall_risk",
            model_version="v1",
            scored_at=past_3d,
            label="LOW",
        ))

        # New but no verdict yet, within 24 h window – NOT a violation
        session.add(McpServerRegistry(
            server_id="new-within-sla",
            name="New Within SLA",
            first_seen=past_8h,
            last_assessed=None,
            risk_tier=None,
        ))

        # New, no verdict, OVER 24 h window – VIOLATION type 1
        session.add(McpServerRegistry(
            server_id="new-never-verdicted",
            name="New Never Verdicted",
            first_seen=past_30d,
            last_assessed=None,
            risk_tier=None,
        ))

        # Has verdict, but last scored 10 days ago – VIOLATION type 2
        session.add(McpServerRegistry(
            server_id="live-stale-server",
            name="Live Stale Server",
            first_seen=past_30d,
            last_assessed=past_10d,
            risk_tier="high",
            verdict="HIGH",
        ))
        session.add(McpLlmAxisScore(
            id=2,
            server_id="live-stale-server",
            axis_name="overall_risk",
            model_version="v1",
            scored_at=past_10d,
            label="HIGH",
        ))

        # Healthy live server – scored 3 days ago, within SLA
        session.add(McpServerRegistry(
            server_id="healthy-server",
            name="Healthy Server",
            first_seen=past_30d,
            last_assessed=past_3d,
            risk_tier="low",
        ))
        session.add(McpLlmAxisScore(
            id=3,
            server_id="healthy-server",
            axis_name="overall_risk",
            model_version="v1",
            scored_at=past_3d,
            label="LOW",
        ))

        session.commit()

    session = TestSession()
    seed(session)
    session.close()

    # ---------------------------------------------------------------------------
    # Build minimal FastAPI app with the router + dependency overrides
    # ---------------------------------------------------------------------------
    def override_get_session():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    def mock_admin_principal() -> Principal:
        return Principal(user_id="test", org_id="test-org", role="admin")

    def mock_member_principal() -> Principal:
        return Principal(user_id="test", org_id="test-org", role="member")

    client = TestClient(app, raise_server_exceptions=False)

    # ---------------------------------------------------------------------------
    # Test 1 – Happy path: violations returned, counts correct, IDs + reasons OK
    # ---------------------------------------------------------------------------
    app.dependency_overrides[get_principal] = mock_admin_principal

    resp = client.get("/audit/signal-freshness")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    body = resp.json()
    try:
        assert body["total_violations"] == 2, f"Expected 2 violations, got {body}"
        assert body["new_mcp_violations_24h"] == 1, f"Expected 1 new-mcp violation, got {body}"
        assert body["live_stale_violations_7d"] == 1, f"Expected 1 live-stale violation, got {body}"
        assert body["total_servers_audited"] == 5, f"Expected 5 servers audited, got {body}"

        server_ids = {r["server_id"] for r in body["servers"]}
        assert "new-never-verdicted" in server_ids, f"Missing new-never-verdicted: {body}"
        assert "live-stale-server" in server_ids, f"Missing live-stale-server: {body}"

        reason_map = {r["server_id"]: r["stale_reason"] for r in body["servers"]}
        assert reason_map["new-never-verdicted"] == "new_mcp_no_verdict", body
        assert reason_map["live-stale-server"] == "live_stale_7d", body
    except AssertionError as e:
        print(f"FAIL: assertion error: {e}")
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # Test 2 – RBAC: non-admin role gets 403
    # ---------------------------------------------------------------------------
    app.dependency_overrides[get_principal] = mock_member_principal

    resp2 = client.get("/audit/signal-freshness")
    if resp2.status_code != 403:
        print(f"FAIL: expected 403 for member role, got {resp2.status_code}: {resp2.text}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
