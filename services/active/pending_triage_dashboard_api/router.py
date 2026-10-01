# deps: fastapi, pydantic, sqlalchemy
"""pending_triage_dashboard_api -- servers in HIGH_RISK_ISOLATED / CAUTION_LIMITED
tiers that have not been assessed within a configurable window and have no active
dispute awaiting resolution.

GET /api/triage/pending
  Returns servers that need manual triage, with tier breakdown and sample rows.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, exists, func, not_, select
from sqlalchemy.orm import Session

# Ensure repo root on path for app.* imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api", tags=["pending_triage_dashboard_api"])


# --------------------------------------------------------------------------- #
# Pydantic response shapes
# --------------------------------------------------------------------------- #

class PendingServerItem(BaseModel):
    server_id: str
    name: str | None
    risk_tier: str | None
    last_assessed: datetime | None
    last_scanned: datetime | None
    days_since_assessment: int | None


class TierBreakdownItem(BaseModel):
    risk_tier: str
    count: int


class PendingTriageSummary(BaseModel):
    pending_triage_count: int
    by_tier: list[TierBreakdownItem]


class PendingTriageResponse(BaseModel):
    as_of: str
    summary: PendingTriageSummary
    items: list[PendingServerItem]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _days_since(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return (now - aware).days


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get("/triage/pending", response_model=PendingTriageResponse)
def get_pending_triage(
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    days_threshold: int = Query(default=30, ge=1, le=365),
) -> PendingTriageResponse:
    """
    Servers in HIGH_RISK_ISOLATED or CAUTION_LIMITED that have not been assessed
    within `days_threshold` days and have no active (non-REJECTED) dispute on record.
    Results are ordered by last_assessed ASC NULLS FIRST so the oldest, most
    stale items appear first.
    """
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=days_threshold)

    # Sub-query: server_ids that have an *active* dispute (status != REJECTED)
    active_dispute_subq = (
        select(McpScoreDispute.server_id)
        .where(McpScoreDispute.server_id == McpServerRegistry.server_id)
        .where(McpScoreDispute.status != "REJECTED")
    ).scalar_subquery()

    # Main filter: needs-triage tier AND (never assessed OR assessed before threshold)
    # AND no active dispute
    needs_triage_filter = and_(
        McpServerRegistry.risk_tier.in_(["HIGH_RISK_ISOLATED", "CAUTION_LIMITED"]),
        or_(
            McpServerRegistry.last_assessed.is_(None),
            McpServerRegistry.last_assessed < threshold,
        ),
        not_(exists(active_dispute_subq)),
    )

    # Total count grouped by tier
    count_q = (
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .where(needs_triage_filter)
        .group_by(McpServerRegistry.risk_tier)
    )
    count_rows = session.execute(count_q).all()

    total_pending = sum(row.count for row in count_rows)
    by_tier = [
        TierBreakdownItem(risk_tier=row.risk_tier or "UNKNOWN", count=row.count)
        for row in count_rows
    ]

    # Paginated server rows
    servers_q = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            McpServerRegistry.last_scanned,
        )
        .where(needs_triage_filter)
        .order_by(McpServerRegistry.last_assessed.asc().nullslast())
        .limit(limit)
        .offset(offset)
    )
    server_rows = session.execute(servers_q).all()

    items = [
        PendingServerItem(
            server_id=row.server_id,
            name=row.name,
            risk_tier=row.risk_tier,
            last_assessed=row.last_assessed,
            last_scanned=row.last_scanned,
            days_since_assessment=_days_since(row.last_assessed),
        )
        for row in server_rows
    ]

    return PendingTriageResponse(
        as_of=now.isoformat(),
        summary=PendingTriageSummary(
            pending_triage_count=total_pending,
            by_tier=by_tier,
        ),
        items=items,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)
    stale = now - timedelta(days=45)
    recent = now - timedelta(days=10)
    very_old = now - timedelta(days=90)

    test_data = [
        # (server_id, name, tier, last_assessed, has_active_dispute)
        ("srv-001", "Stale High Risk", "HIGH_RISK_ISOLATED", very_old, False),
        ("srv-002", "Stale Caution", "CAUTION_LIMITED", stale, False),
        ("srv-003", "Stale High Rejected", "HIGH_RISK_ISOLATED", very_old, True),
        ("srv-004", "Recent High", "HIGH_RISK_ISOLATED", recent, False),
        ("srv-005", "Recent Caution", "CAUTION_LIMITED", recent, False),
        ("srv-006", "Safe Server", "LOW_RISK_STANDARD", stale, False),
    ]

    with TestSession() as sess:
        for sid, name, tier, assessed, has_dispute in test_data:
            sess.add(McpServerRegistry(
                server_id=sid,
                name=name,
                risk_tier=tier,
                last_assessed=assessed,
                last_scanned=assessed,
                trust_score=50.0,
                confidence=0.7,
                registry_source="test",
                url=f"https://example.com/{sid}",
            ))
            sess.flush()
            if has_dispute:
                sess.add(McpScoreDispute(
                    server_id=sid,
                    submitted_by="test_user",
                    reason_category="test",
                    status="REJECTED",  # rejected disputes should NOT block triage
                    explanation="test",
                ))

        # Add an active (PENDING) dispute for srv-007
        sess.add(McpServerRegistry(
            server_id="srv-007",
            name="Active Dispute Server",
            risk_tier="HIGH_RISK_ISOLATED",
            last_assessed=stale,
            last_scanned=stale,
            trust_score=50.0,
            confidence=0.7,
            registry_source="test",
            url="https://example.com/srv-007",
        ))
        sess.flush()
        sess.add(McpScoreDispute(
            server_id="srv-007",
            submitted_by="test_user",
            reason_category="test",
            status="PENDING",
            explanation="test",
        ))
        sess.commit()

    client = TestClient(app)

    # 1. Happy path: returns pending servers
    resp = client.get("/api/triage/pending?limit=10")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    # srv-001 (stale high, no dispute) -> pending
    # srv-002 (stale caution, no dispute) -> pending
    # srv-003 REJECTED dispute -> pending (only REJECTED is excluded from the
    #   filter; we treat it as not blocking)
    # srv-004 recent high -> NOT pending
    # srv-005 recent caution -> NOT pending
    # srv-006 low risk -> NOT pending (wrong tier)
    # srv-007 active PENDING dispute -> NOT pending (active dispute blocks)
    pending_ids = {item["server_id"] for item in data["items"]}
    assert "srv-001" in pending_ids, f"srv-001 should be pending, got: {pending_ids}"
    assert "srv-002" in pending_ids, f"srv-002 should be pending, got: {pending_ids}"
    assert "srv-004" not in pending_ids, f"srv-004 (recent) should NOT be pending"
    assert "srv-006" not in pending_ids, f"srv-006 (wrong tier) should NOT be pending"
    assert "srv-007" not in pending_ids, f"srv-007 (active dispute) should NOT be pending"

    # Summary count
    assert data["summary"]["pending_triage_count"] == 3, (
        f"Expected 3 pending, got {data['summary']['pending_triage_count']}"
    )

    # Tier breakdown present
    by_tier = {t["risk_tier"]: t["count"] for t in data["summary"]["by_tier"]}
    assert by_tier.get("HIGH_RISK_ISOLATED") == 2, f"HIGH expected 2, got {by_tier}"
    assert by_tier.get("CAUTION_LIMITED") == 1, f"CAUTION expected 1, got {by_tier}"

    # 2. days_threshold param narrows results
    resp_narrow = client.get("/api/triage/pending?days_threshold=60")
    assert resp_narrow.status_code == 200
    data_narrow = resp_narrow.json()
    # With 60-day threshold, only very_old (90d) items qualify
    assert data_narrow["summary"]["pending_triage_count"] == 2, (
        f"Expected 2 at 60d threshold, got {data_narrow['summary']['pending_triage_count']}"
    )

    # 3. Pagination: offset works
    resp_page = client.get("/api/triage/pending?limit=1&offset=0")
    assert resp_page.status_code == 200
    data_page = resp_page.json()
    assert len(data_page["items"]) == 1, f"Expected 1 item at offset=0, got {len(data_page['items'])}"

    resp_page2 = client.get("/api/triage/pending?limit=1&offset=1")
    assert resp_page2.status_code == 200
    data_page2 = resp_page2.json()
    assert len(data_page2["items"]) == 1
    assert data_page2["items"][0]["server_id"] != data_page["items"][0]["server_id"]

    print("PASS")
