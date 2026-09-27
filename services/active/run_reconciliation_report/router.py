# deps: fastapi, pydantic, sqlalchemy
"""run_reconciliation_report -- reconciles servers across the registry vs axis scores.

Produces a per-server reconciliation report: which servers in the registry have
no axis-score rows, which have fewer or more than the expected 7 axes, and
which have stale scores (last_scored_at older than threshold).

Auth: public (auth=public in directive).
Data: app tier via get_session + SQLAlchemy ORM on
  McpServerRegistry, McpLlmAxisScore.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["run_reconciliation_report"])

# Expected number of axes per server (the 7-axis SFT schema).
EXPECTED_AXES_PER_SERVER = 7

# Stale threshold: a score is considered stale if scored_at is older than this.
STALE_THRESHOLD_DAYS = 7


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class ReconciliationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str] = None
    issue: str = Field(..., description="Type of reconciliation issue")
    detail: str = Field(..., description="Human-readable detail")
    axis_count: int = Field(default=0, description="Number of axis scores found")
    last_scored_at: Optional[datetime] = None


class ReconciliationSummary(BaseModel):
    total_servers: int = Field(default=0)
    servers_with_issues: int = Field(default=0)
    no_axis_scores: int = Field(default=0, description="Servers missing all axis scores")
    wrong_axis_count: int = Field(default=0, description="Servers with != 7 axes")
    stale_scores: int = Field(default=0, description="Servers with stale axis scores")
    healthy: int = Field(default=0, description="Servers with clean reconciliation")


class ReconciliationReport(BaseModel):
    summary: ReconciliationSummary
    issues: list[ReconciliationItem] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #

def build_reconciliation(session: Session, stale_days: int = STALE_THRESHOLD_DAYS) -> ReconciliationReport:
    """Query registry + axis scores and compute the reconciliation report."""
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=stale_days)

    # Fetch all registry servers
    servers = session.query(McpServerRegistry).all()

    issues: list[ReconciliationItem] = []
    no_axis = 0
    wrong_axis = 0
    stale = 0
    healthy = 0

    for srv in servers:
        # Count axis scores for this server
        axis_rows = (
            session.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == srv.server_id)
            .all()
        )
        axis_count = len(axis_rows)

        if axis_count == 0:
            no_axis += 1
            issues.append(ReconciliationItem(
                server_id=srv.server_id,
                name=srv.name,
                issue="no_axis_scores",
                detail="Server exists in registry but has no axis score rows.",
                axis_count=0,
                last_scored_at=None,
            ))
            continue

        # Check axis count
        if axis_count != EXPECTED_AXES_PER_SERVER:
            wrong_axis += 1
            issues.append(ReconciliationItem(
                server_id=srv.server_id,
                name=srv.name,
                issue="wrong_axis_count",
                detail=f"Expected {EXPECTED_AXES_PER_SERVER} axes, found {axis_count}.",
                axis_count=axis_count,
                last_scored_at=None,
            ))

        # Check staleness: most recent scored_at across axes
        latest = max((r.scored_at for r in axis_rows if r.scored_at), default=None)
        if latest is not None and latest < stale_cutoff:
            stale += 1
            issues.append(ReconciliationItem(
                server_id=srv.server_id,
                name=srv.name,
                issue="stale_scores",
                detail=f"Last scored at {latest.isoformat()}, older than {stale_days} days.",
                axis_count=axis_count,
                last_scored_at=latest,
            ))
            continue

        # All checks passed
        healthy += 1

    summary = ReconciliationSummary(
        total_servers=len(servers),
        servers_with_issues=len(issues),
        no_axis_scores=no_axis,
        wrong_axis_count=wrong_axis,
        stale_scores=stale,
        healthy=healthy,
    )
    return ReconciliationReport(summary=summary, issues=issues)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/reconciliation/report",
    response_model=ReconciliationReport,
    name="run_reconciliation_report:get",
    summary="Get server reconciliation report",
    responses={
        200: {"description": "Reconciliation report with per-server issues"},
    },
)
def get_reconciliation_report(
    stale_days: int = Query(
        default=STALE_THRESHOLD_DAYS,
        ge=1,
        le=365,
        description="Consider scores stale after this many days without a new score.",
    ),
    db: Session = Depends(get_session),
) -> ReconciliationReport:
    """
    Reconcile mcp_server_registry against mcp_llm_axis_scores.

    Checks for:
      - servers with no axis scores at all
      - servers with a non-standard axis count (!=7)
      - servers whose most-recent axis score is older than *stale_days*
    """
    return build_reconciliation(db, stale_days=stale_days)


# --------------------------------------------------------------------------- #
# __main__ self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys, pathlib

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # In-memory SQLite test DB (matches app.db patterns; NOT DuckDB)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ---------------------------------------------------------------------------
    # Seed test data
    # ---------------------------------------------------------------------------
    now = datetime.now(timezone.utc)

    # Server with no axis scores
    from app.models import McpServerRegistry, McpLlmAxisScore

    db = TestingSession()
    db.add(McpServerRegistry(
        server_id="srv-no-scores",
        name="No Scores Server",
        registry_source="test",
        url="http://example.com/no-scores",
    ))

    # Server with 7 healthy axes
    db.add(McpServerRegistry(
        server_id="srv-healthy",
        name="Healthy Server",
        registry_source="test",
        url="http://example.com/healthy",
    ))
    for i, axis in enumerate(["overall_risk", "auth_strength", "capability_breadth",
                               "data_sensitivity", "network_egress",
                               "maintainer_trust", "exploit_surface"]):
        db.add(McpLlmAxisScore(
            server_id="srv-healthy",
            axis_name=axis,
            label="LOW",
            label_index=1,
            p_top=0.9,
            p_critical=0.05,
            p_danger=0.05,
            escalated=False,
            model_version="v1",
            scored_at=now,
        ))

    # Server with only 3 axes (wrong count)
    db.add(McpServerRegistry(
        server_id="srv-few-axes",
        name="Few Axes Server",
        registry_source="test",
        url="http://example.com/few",
    ))
    for axis in ["overall_risk", "auth_strength", "capability_breadth"]:
        db.add(McpLlmAxisScore(
            server_id="srv-few-axes",
            axis_name=axis,
            label="MEDIUM",
            label_index=2,
            p_top=0.5,
            p_critical=0.3,
            p_danger=0.2,
            escalated=False,
            model_version="v1",
            scored_at=now,
        ))

    # Server with stale scores (scored 30 days ago)
    db.add(McpServerRegistry(
        server_id="srv-stale",
        name="Stale Server",
        registry_source="test",
        url="http://example.com/stale",
    ))
    old = now - timedelta(days=30)
    for axis in ["overall_risk", "auth_strength", "capability_breadth",
                 "data_sensitivity", "network_egress", "maintainer_trust",
                 "exploit_surface"]:
        db.add(McpLlmAxisScore(
            server_id="srv-stale",
            axis_name=axis,
            label="HIGH",
            label_index=3,
            p_top=0.2,
            p_critical=0.6,
            p_danger=0.2,
            escalated=True,
            model_version="v1",
            scored_at=old,
        ))

    db.commit()
    db.close()

    # ---------------------------------------------------------------------------
    # Run tests
    # ---------------------------------------------------------------------------
    client = TestClient(app)

    # Happy path: report with all issue types
    r = client.get("/api/reconciliation/report?stale_days=7")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "summary" in body, f"Missing summary: {body}"
    assert "issues" in body, f"Missing issues: {body}"
    s = body["summary"]
    assert s["total_servers"] == 4, f"Expected 4 total servers, got {s['total_servers']}"
    assert s["no_axis_scores"] == 1, f"Expected 1 no-axis issue, got {s['no_axis_scores']}"
    assert s["wrong_axis_count"] == 1, f"Expected 1 wrong-axis issue, got {s['wrong_axis_count']}"
    assert s["stale_scores"] == 1, f"Expected 1 stale issue, got {s['stale_scores']}"
    assert s["healthy"] == 1, f"Expected 1 healthy server, got {s['healthy']}"

    # Check issue types are present
    issues_by_type = {item["issue"] for item in body["issues"]}
    assert "no_axis_scores" in issues_by_type
    assert "wrong_axis_count" in issues_by_type
    assert "stale_scores" in issues_by_type

    # Custom stale_days param
    r2 = client.get("/api/reconciliation/report?stale_days=60")
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    # With 60-day threshold, srv-stale is no longer stale
    assert body2["summary"]["stale_scores"] == 0, \
        f"Expected 0 stale with 60d threshold, got {body2['summary']['stale_scores']}"

    # Empty DB (fresh session with no data beyond what we added -- happy-path empty state)
    empty_db = TestingSession()
    # Wipe all data
    empty_db.query(McpLlmAxisScore).delete()
    empty_db.query(McpServerRegistry).delete()
    empty_db.commit()
    empty_db.close()

    # Override to a session against the same engine (tables are empty now)
    def _empty_override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _empty_override
    r3 = client.get("/api/reconciliation/report")
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    assert body3["summary"]["total_servers"] == 0
    assert body3["summary"]["healthy"] == 0

    print("PASS")
    sys.exit(0)
