# services/staged/freshness_metadata/contract.py
"""
Freshness metadata service contract.

Provides:
GET /api/servers/{server_id}/freshness
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Real data layer imports (must stay unchanged for production)
# ----------------------------------------------------------------------
from app.db import get_session, Base  # noqa: F401
from app.models import McpServerRegistry, McpLlmAxisScore  # noqa: F401

# ----------------------------------------------------------------------
# Pydantic response model
# ----------------------------------------------------------------------
class FreshnessResponse(BaseModel):
    server_id: str
    last_scanned: Optional[datetime] = None
    scan_count: Optional[int] = None
    last_axis_score_at: Optional[datetime] = None
    hours_since_score: Optional[float] = None
    score_stale_hours: Optional[float] = None
    scan_stale_hours: Optional[float] = None
    overall_fresh: Literal["fresh", "stale", "unknown"]


# ----------------------------------------------------------------------
# FastAPI router
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/freshness",
    response_model=FreshnessResponse,
    name="get_freshness",
)
def get_freshness(
    server_id: str,
    db: Session = Depends(get_session),
) -> FreshnessResponse:
    """Return freshness information for a given server."""
    # ------------------------------------------------------------------
    # Server registry row
    # ------------------------------------------------------------------
    server_stmt = (
        select(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
        .limit(1)
    )
    server_row: Optional[McpServerRegistry] = db.execute(server_stmt).scalar_one_or_none()
    if server_row is None:
        raise HTTPException(status_code=404, detail="Server not found")

    # ------------------------------------------------------------------
    # Latest axis score timestamp
    # ------------------------------------------------------------------
    score_stmt = (
        select(func.max(McpLlmAxisScore.scored_at))
        .where(McpLlmAxisScore.server_id == server_id)
        .scalar_subquery()
    )
    last_axis_score_at: Optional[datetime] = db.execute(select(score_stmt)).scalar_one()

    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Compute derived fields
    # ------------------------------------------------------------------
    hours_since_score: Optional[float] = None
    score_stale_hours: Optional[float] = None
    if last_axis_score_at:
        delta = now - last_axis_score_at
        hours_since_score = delta.total_seconds() / 3600.0
        score_stale_hours = hours_since_score

    scan_stale_hours: Optional[float] = None
    if server_row.last_scanned:
        delta = now - server_row.last_scanned
        scan_stale_hours = delta.total_seconds() / 3600.0

    # ------------------------------------------------------------------
    # Overall freshness determination
    # ------------------------------------------------------------------
    FRESHNESS_THRESHOLD_HOURS = 24.0
    if (
        hours_since_score is not None
        and scan_stale_hours is not None
        and hours_since_score <= FRESHNESS_THRESHOLD_HOURS
        and scan_stale_hours <= FRESHNESS_THRESHOLD_HOURS
    ):
        overall = "fresh"
    elif hours_since_score is None or scan_stale_hours is None:
        overall = "unknown"
    else:
        overall = "stale"

    return FreshnessResponse(
        server_id=server_id,
        last_scanned=server_row.last_scanned,
        scan_count=server_row.scan_count,
        last_axis_score_at=last_axis_score_at,
        hours_since_score=hours_since_score,
        score_stale_hours=score_stale_hours,
        scan_stale_hours=scan_stale_hours,
        overall_fresh=overall,
    )


# ----------------------------------------------------------------------
# FastAPI application (used by the self‑test)
# ----------------------------------------------------------------------
app = FastAPI()
app.include_router(router)


# ----------------------------------------------------------------------
# Self‑test (run with: python -m services.staged.freshness_metadata.contract)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from datetime import datetime, timezone, timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------
    # Build a throw‑away SQLite DB that mirrors the real models
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # ------------------------------------------------------------------
    # Dependency override to use the test session
    # ------------------------------------------------------------------
    def get_test_session() -> Session:  # pragma: no cover
        with TestSession() as sess:
            yield sess

    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Seed test data
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)

    with TestSession() as sess:
        # server registry entry
        sess.add(
            McpServerRegistry(
                server_id="test-server",
                last_scanned=now - timedelta(hours=5),
                scan_count=3,
            )
        )
        # axis score entry
        sess.add(
            McpLlmAxisScore(
                server_id="test-server",
                scored_at=now - timedelta(hours=2),
            )
        )
        sess.commit()

    # ------------------------------------------------------------------
    # Run the request against the test client
    # ------------------------------------------------------------------
    client = TestClient(app)
    resp = client.get("/api/servers/test-server/freshness")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    # basic sanity checks
    if data["hours_since_score"] is None or data["hours_since_score"] < 0:
        print("FAIL: hours_since_score invalid", file=sys.stderr)
        sys.exit(1)

    if data["overall_fresh"] not in ("fresh", "stale", "unknown"):
        print("FAIL: overall_fresh value invalid", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)