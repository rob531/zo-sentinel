# deps: fastapi, pydantic, sqlalchemy, requests
"""FastAPI router for server-freshness violation reporting.

Queries the app DB (McpServerRegistry, McpLlmAxisScore) for servers that have
breached PRODUCT_SPEC §4 freshness SLAs:
  (a) first verdict within 24h of first_seen
  (b) re-verdicted within 7 days of last_assessed

Public access (auth=public). Data access via SQLAlchemy session dependency.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Generator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_freshness_violation_api"])

FIRST_VERDICT_THRESHOLD_HOURS = 24
REVERDICT_THRESHOLD_HOURS = 24 * 7


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class FreshnessViolation(BaseModel):
    server_id: str
    name: str | None
    violation_type: str  # "first_verdict_overdue" | "reassessment_overdue"
    age_hours: float
    threshold_hours: float


class ViolationsResponse(BaseModel):
    violations: list[FreshnessViolation]
    total: int
    first_verdict_overdue: int
    reassessment_overdue: int


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------


def _check_violations(db: Session) -> list[dict[str, Any]]:
    """Return violation records for servers breaching §4 freshness SLAs."""
    now = datetime.now(timezone.utc)

    # Subquery: earliest score per server
    first_verdict_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.min(McpLlmAxisScore.scored_at).label("first_verdict_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Subquery: latest score per server
    latest_verdict_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_verdict_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    q = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_assessed,
        first_verdict_subq.c.first_verdict_at,
        latest_verdict_subq.c.last_verdict_at,
    ).outerjoin(
        first_verdict_subq,
        McpServerRegistry.server_id == first_verdict_subq.c.server_id,
    ).outerjoin(
        latest_verdict_subq,
        McpServerRegistry.server_id == latest_verdict_subq.c.server_id,
    )

    servers = db.execute(q).all()
    violations: list[dict[str, Any]] = []

    for row in servers:
        server_id: str = row.server_id
        name: str | None = row.name
        first_seen: datetime | None = row.first_seen
        last_assessed: datetime | None = row.last_assessed
        first_verdict_at: datetime | None = row.first_verdict_at
        last_verdict_at: datetime | None = row.last_verdict_at

        if first_seen is None:
            continue

        now_naive = now.replace(tzinfo=None)

        # (a) first verdict within 24h of first_seen
        if first_verdict_at is not None:
            age_hours = (first_verdict_at - first_seen).total_seconds() / 3600
        else:
            age_hours = (now_naive - first_seen).total_seconds() / 3600

        if age_hours > FIRST_VERDICT_THRESHOLD_HOURS:
            violations.append(
                {
                    "server_id": server_id,
                    "name": name,
                    "violation_type": "first_verdict_overdue",
                    "age_hours": round(age_hours, 2),
                    "threshold_hours": float(FIRST_VERDICT_THRESHOLD_HOURS),
                }
            )

        # (b) re-verdicted within 7 days of last_assessed
        #     violation = no score since last_assessed + 7 days
        ref_date = last_verdict_at if last_verdict_at else last_assessed
        if ref_date is None:
            ref_date = first_seen

        age_hours = (now_naive - ref_date).total_seconds() / 3600
        if age_hours > REVERDICT_THRESHOLD_HOURS:
            violations.append(
                {
                    "server_id": server_id,
                    "name": name,
                    "violation_type": "reassessment_overdue",
                    "age_hours": round(age_hours, 2),
                    "threshold_hours": float(REVERDICT_THRESHOLD_HOURS),
                }
            )

    return violations


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/servers/freshness-violations",
    response_model=ViolationsResponse,
    summary="List servers with freshness SLA violations",
    responses={200: {"description": "Violation list"}},
)
def list_freshness_violations(
    db: Session = Depends(get_session),
) -> ViolationsResponse:
    """
    Return all servers that have breached PRODUCT_SPEC §4 freshness SLAs:
    - **first_verdict_overdue**: no verdict within 24 h of first_seen
    - **reassessment_overdue**: no verdict within 7 days of the most recent
      verdict (or last_assessed / first_seen as fallback)
    """
    raw = _check_violations(db)
    first_count = sum(1 for v in raw if v["violation_type"] == "first_verdict_overdue")
    reassess_count = sum(1 for v in raw if v["violation_type"] == "reassessment_overdue")
    return ViolationsResponse(
        violations=[FreshnessViolation(**v) for v in raw],
        total=len(raw),
        first_verdict_overdue=first_count,
        reassessment_overdue=reassign_count,
    )


# ---------------------------------------------------------------------------
# Self-test (runs against an in-memory SQLite DB, no live Postgres needed)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build isolated test engine + session
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create tables from the real model metadata
    McpServerRegistry.metadata.create_all(test_engine)
    McpLlmAxisScore.metadata.create_all(test_engine)

    TestSession: sessionmaker = sessionmaker(bind=test_engine)

    def _override_session() -> Generator[Session, None, None]:
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    # Seed test data
    now = datetime.now(timezone.utc)
    with TestSession() as sess:
        # Server with no verdict yet, first_seen 48 h ago → first_verdict_overdue
        srv1 = McpServerRegistry(
            server_id="srv-no-verdict",
            name="No Verdict Server",
            first_seen=now - timedelta(hours=48),
            last_assessed=None,
            registry_source="test",
            url="http://srv1.test",
        )
        # Server with verdict 10 days ago, last_assessed 10 days ago → reassessment_overdue
        srv2 = McpServerRegistry(
            server_id="srv-stale-reassess",
            name="Stale Reassessment Server",
            first_seen=now - timedelta(days=30),
            last_assessed=now - timedelta(days=10),
            registry_source="test",
            url="http://srv2.test",
        )
        # Server with verdict 2 h ago → compliant
        srv3 = McpServerRegistry(
            server_id="srv-compliant",
            name="Compliant Server",
            first_seen=now - timedelta(days=2),
            last_assessed=now - timedelta(hours=2),
            registry_source="test",
            url="http://srv3.test",
        )
        sess.add_all([srv1, srv2, srv3])

        # Scores
        # srv2: old score from 10 days ago (triggers reassessment_overdue)
        sess.add(
            McpLlmAxisScore(
                server_id="srv-stale-reassess",
                axis_name="safety",
                label="LOW",
                label_index=1,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="abc123",
                scored_at=now - timedelta(days=10),
                p_critical=0.0,
                p_danger=0.1,
                p_top=0.9,
                escalated=False,
            )
        )
        # srv3: recent score 2 h ago (compliant)
        sess.add(
            McpLlmAxisScore(
                server_id="srv-compliant",
                axis_name="safety",
                label="LOW",
                label_index=1,
                model_version="v1",
                decision_rule_version="r1",
                adapter_sha256="def456",
                scored_at=now - timedelta(hours=2),
                p_critical=0.0,
                p_danger=0.1,
                p_top=0.9,
                escalated=False,
            )
        )
        sess.commit()

    # Build FastAPI app and wire override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)

    # Happy path: violations returned
    resp = client.get("/api/servers/freshness-violations")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "violations" in data, "Missing 'violations' key"
    violations = data["violations"]
    assert data["total"] == len(violations), "total must equal len(violations)"

    first_types = [v for v in violations if v["violation_type"] == "first_verdict_overdue"]
    reassess_types = [v for v in violations if v["violation_type"] == "reassessment_overdue"]

    assert len(first_types) == 1, f"Expected 1 first_verdict_overdue, got {len(first_types)}"
    assert len(reassign_types) == 1, f"Expected 1 reassessment_overdue, got {len(reassign_types)}"
    assert len(violations) == 2, f"Expected 2 total violations, got {len(violations)}"

    # srv1 triggers first_verdict_overdue
    srv1_v = next((v for v in violations if v["server_id"] == "srv-no-verdict"), None)
    assert srv1_v is not None, "srv-no-verdict should appear"
    assert srv1_v["violation_type"] == "first_verdict_overdue"
    assert srv1_v["threshold_hours"] == 24.0

    # srv2 triggers reassessment_overdue
    srv2_v = next((v for v in violations if v["server_id"] == "srv-stale-reassess"), None)
    assert srv2_v is not None, "srv-stale-reassess should appear"
    assert srv2_v["violation_type"] == "reassessment_overdue"
    assert srv2_v["threshold_hours"] == 168.0

    # srv3 is compliant (no violations)
    assert not any(v["server_id"] == "srv-compliant" for v in violations), \
        "srv-compliant should have no violations"

    print("PASS")
    sys.exit(0)
