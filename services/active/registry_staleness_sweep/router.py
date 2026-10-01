# deps: fastapi, pydantic, sqlalchemy
"""Registry Staleness Sweep – surfaces servers that have exceeded the re-verdict SLA.

GET /api/registry/staleness-sweep
  Returns all servers bucketed by freshness: ok, warning, critical, never_scored.
  Buckets are computed from the most recent mcp_llm_axis_scores entry per server.

Auth: public.
Data: app-db via get_session + SQLAlchemy ORM on mcp_server_registry / mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry_staleness_sweep"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class ServerStalenessItem(BaseModel):
    server_id: str
    name: Optional[str]
    hours_since_assessed: float
    tier: Optional[str]
    last_scored_at: Optional[str]

    model_config = {"from_attributes": True}


class StalenessSummary(BaseModel):
    ok: int
    warning: int
    critical: int
    never_scored: int


class StalenessSweepResponse(BaseModel):
    sweep_at: str = Field(..., description="ISO-8601 timestamp of report generation")
    sla_hours: int = Field(..., description="SLA window used (hours)")
    summary: StalenessSummary
    servers: list[ServerStalenessItem]


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #

def staleness_sweep_logic(
    db: Session,
    sla_hours: int = 24,
) -> StalenessSweepResponse:
    """Classify every registered server by freshness and return a sorted list."""
    now = datetime.now(timezone.utc)
    warning_threshold = sla_hours
    critical_threshold = sla_hours * 2

    # Most-recent score per server (ORM subquery)
    latest_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # All servers left-joined to their most-recent score
    rows = (
        db.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.name,
                McpServerRegistry.risk_tier,
                latest_subq.c.last_scored_at,
            )
            .outerjoin(
                latest_subq,
                McpServerRegistry.server_id == latest_subq.c.server_id,
            )
            .where(McpServerRegistry.registry_source.isnot(None))
        )
        .fetchall()
    )

    servers: list[ServerStalenessItem] = []
    summary = {"ok": 0, "warning": 0, "critical": 0, "never_scored": 0}

    for row in rows:
        server_id = row[0]
        name = row[1]
        tier = row[2]
        last_scored_at = row[3]

        if last_scored_at is None:
            category = "never_scored"
            hours_since = 0.0
            last_scored_iso: Optional[str] = None
        else:
            # Ensure last_scored_at is timezone-aware before subtraction
            scored_ts = last_scored_at
            if scored_ts.tzinfo is None:
                scored_ts = scored_ts.replace(tzinfo=timezone.utc)
            delta = now - scored_ts
            hours_since = delta.total_seconds() / 3600.0
            last_scored_iso = scored_ts.isoformat()
            if hours_since <= warning_threshold:
                category = "ok"
            elif hours_since <= critical_threshold:
                category = "warning"
            else:
                category = "critical"

        servers.append(
            ServerStalenessItem(
                server_id=server_id,
                name=name,
                hours_since_assessed=round(hours_since, 2),
                tier=tier,
                last_scored_at=last_scored_iso,
            )
        )
        summary[category] += 1

    # Sort: critical first, then warning, then ok, then never_scored (desc hours)
    servers.sort(
        key=lambda s: (
            0 if s.hours_since_assessed > critical_threshold else
            1 if s.hours_since_assessed > warning_threshold else
            2 if s.last_scored_at is not None else 3,
            -(s.hours_since_assessed),
        )
    )

    return StalenessSweepResponse(
        sweep_at=now.isoformat(),
        sla_hours=sla_hours,
        summary=StalenessSummary(**summary),
        servers=servers,
    )


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/registry/staleness-sweep",
    response_model=StalenessSweepResponse,
    summary="Get registry staleness sweep",
)
def staleness_sweep(
    hours: int = Query(24, ge=1, le=720, description="SLA window in hours"),
    db: Session = Depends(get_session),
) -> StalenessSweepResponse:
    """
    Return all servers bucketed by freshness against the given SLA window.

    - **hours**: SLA window in hours (default 24).
      - ok: ≤ hours
      - warning: > hours and ≤ 2×hours
      - critical: > 2×hours
      - never_scored: no score record
    """
    return staleness_sweep_logic(db=db, sla_hours=hours)


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

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override_get_session() -> Generator[Session, None, None]:
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.now(timezone.utc)

    with TestSessionLocal() as sess:
        # srv_critical: scored 55h ago (> 48h = critical)
        sess.add(
            McpServerRegistry(
                server_id="srv_critical",
                name="Critical Server",
                risk_tier="high",
                registry_source="test",
                url="http://crit.example.com",
            )
        )
        sess.add(
            McpLlmAxisScore(
                id=1,
                server_id="srv_critical",
                axis_name="verdict",
                label="high",
                label_index=3,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="deadbeef",
                probs=None,
                p_critical=0.9,
                p_danger=0.05,
                p_top=0.05,
                escalated=False,
                scored_at=now - __import__("datetime").timedelta(hours=55),
            )
        )

        # srv_warning: scored 25h ago (> 24h = warning)
        sess.add(
            McpServerRegistry(
                server_id="srv_warning",
                name="Warning Server",
                risk_tier="medium",
                registry_source="test",
                url="http://warn.example.com",
            )
        )
        sess.add(
            McpLlmAxisScore(
                id=2,
                server_id="srv_warning",
                axis_name="verdict",
                label="medium",
                label_index=2,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="cafebabe",
                probs=None,
                p_critical=0.1,
                p_danger=0.7,
                p_top=0.2,
                escalated=False,
                scored_at=now - __import__("datetime").timedelta(hours=25),
            )
        )

        # srv_ok: scored 10h ago (<= 24h = ok)
        sess.add(
            McpServerRegistry(
                server_id="srv_ok",
                name="OK Server",
                risk_tier="low",
                registry_source="test",
                url="http://ok.example.com",
            )
        )
        sess.add(
            McpLlmAxisScore(
                id=3,
                server_id="srv_ok",
                axis_name="verdict",
                label="low",
                label_index=0,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="beefdead",
                probs=None,
                p_critical=0.0,
                p_danger=0.1,
                p_top=0.9,
                escalated=False,
                scored_at=now - __import__("datetime").timedelta(hours=10),
            )
        )

        # srv_never: registered but never scored
        sess.add(
            McpServerRegistry(
                server_id="srv_never",
                name="Never Scored Server",
                risk_tier=None,
                registry_source="test",
                url="http://never.example.com",
            )
        )

        # srv_recent: scored 30 min ago (fresh)
        sess.add(
            McpServerRegistry(
                server_id="srv_recent",
                name="Recent Server",
                risk_tier="low",
                registry_source="test",
                url="http://recent.example.com",
            )
        )
        sess.add(
            McpLlmAxisScore(
                id=4,
                server_id="srv_recent",
                axis_name="verdict",
                label="low",
                label_index=0,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="abc123",
                probs=None,
                p_critical=0.0,
                p_danger=0.05,
                p_top=0.95,
                escalated=False,
                scored_at=now - __import__("datetime").timedelta(minutes=30),
            )
        )

        sess.commit()

    client = TestClient(test_app)

    resp = client.get("/api/registry/staleness-sweep?hours=24")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()

    assert data["sla_hours"] == 24, f"sla_hours expected 24, got {data['sla_hours']}"
    assert data["summary"]["ok"] == 2, f"ok expected 2, got {data['summary']['ok']}"
    assert data["summary"]["warning"] == 1, f"warning expected 1, got {data['summary']['warning']}"
    assert data["summary"]["critical"] == 1, f"critical expected 1, got {data['summary']['critical']}"
    assert data["summary"]["never_scored"] == 1, f"never_scored expected 1, got {data['summary']['never_scored']}"

    # srv_critical should appear first (most stale)
    assert data["servers"][0]["server_id"] == "srv_critical", \
        f"Expected srv_critical first, got {data['servers'][0]['server_id']}"

    # srv_never should have hours_since_assessed == 0 and null last_scored_at
    never_rows = [s for s in data["servers"] if s["server_id"] == "srv_never"]
    assert len(never_rows) == 1
    assert never_rows[0]["last_scored_at"] is None
    assert never_rows[0]["tier"] is None

    print("PASS")
