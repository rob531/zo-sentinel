"""escalation_timeline_api.py -- chronological history of axis escalations per server.

GET /servers/{server_id}/escalation-timeline
Reads perspective_events (change_type='escalation') joined with mcp_llm_axis_scores
for escalated=True rows. Returns oldest-first list of escalation events with axis detail.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, join
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, McpLlmAxisScore

router = APIRouter(prefix="/servers", tags=["escalation"])


class EscalationEvent(BaseModel):
    id: int
    axis_name: Optional[str] = None
    axis_label: Optional[str] = None
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    escalated_to: Optional[str] = None
    created_at: datetime


class EscalationTimelineResponse(BaseModel):
    server_id: str
    events: list[EscalationEvent]


@router.get("/{server_id}/escalation-timeline", response_model=EscalationTimelineResponse)
def get_escalation_timeline(
    server_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> EscalationTimelineResponse:
    """Chronological history of axis escalations for a server, oldest first.

    Returns perspective_events rows where change_type='escalation', enriched with
    axis_name / axis_label from the matching mcp_llm_axis_scores row.
    """
    # Join perspective_events (change_type='escalation') with mcp_llm_axis_scores
    # on server_id + new_tier = escalated_to so each event carries its axis context.
    j = join(
        PerspectiveEvent,
        McpLlmAxisScore,
        and_(
            PerspectiveEvent.server_id == McpLlmAxisScore.server_id,
            PerspectiveEvent.new_tier == McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.escalated == True,  # noqa: E712
        ),
    )
    stmt = (
        select(
            PerspectiveEvent.id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            McpLlmAxisScore.escalated_to,
            PerspectiveEvent.created_at,
        )
        .select_from(j)
        .where(
            PerspectiveEvent.server_id == server_id,
            PerspectiveEvent.change_type == "escalation",
        )
        .order_by(PerspectiveEvent.created_at.asc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    events = [
        EscalationEvent(
            id=r.id,
            axis_name=r.axis_name,
            axis_label=r.label,
            old_tier=r.old_tier,
            new_tier=r.new_tier,
            escalated_to=r.escalated_to,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return EscalationTimelineResponse(server_id=server_id, events=events)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TS()
    # Seed: server "srv1" with 3 escalation events across 2 axes
    # Event 1: overall_risk escalated LOW -> MEDIUM
    s.add(
        McpLlmAxisScore(
            id=1, server_id="srv1", axis_name="overall_risk",
            label="MEDIUM", model_version="v3.0_40974559",
            escalated=True, escalated_to="MEDIUM",
        )
    )
    s.add(
        PerspectiveEvent(
            perspective_id="p1", server_id="srv1",
            change_type="escalation", old_tier="LOW", new_tier="MEDIUM",
        )
    )
    # Event 2: data_sensitivity escalated LOW -> HIGH
    s.add(
        McpLlmAxisScore(
            id=2, server_id="srv1", axis_name="data_sensitivity",
            label="HIGH", model_version="v3.0_40974559",
            escalated=True, escalated_to="HIGH",
        )
    )
    s.add(
        PerspectiveEvent(
            perspective_id="p1", server_id="srv1",
            change_type="escalation", old_tier="LOW", new_tier="HIGH",
        )
    )
    # Event 3: overall_risk de-escalated MEDIUM -> LOW
    # Use different model_version to avoid unique constraint violation
    s.add(
        McpLlmAxisScore(
            id=3, server_id="srv1", axis_name="overall_risk",
            label="LOW", model_version="v3.0_40974560",
            escalated=False, escalated_to=None,
        )
    )
    s.add(
        PerspectiveEvent(
            perspective_id="p1", server_id="srv1",
            change_type="escalation", old_tier="MEDIUM", new_tier="LOW",
        )
    )
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Happy path: 2 escalation events (escalated=True rows joined)
    r = c.get("/servers/srv1/escalation-timeline")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv1", j
    assert len(j["events"]) == 2, f"Expected 2 events, got {len(j['events'])}: {j['events']}"
    assert j["events"][0]["old_tier"] == "LOW", j["events"][0]
    assert j["events"][0]["new_tier"] == "MEDIUM", j["events"][0]
    assert j["events"][0]["axis_name"] == "overall_risk", j["events"][0]
    assert j["events"][0]["axis_label"] == "MEDIUM", j["events"][0]
    assert j["events"][1]["old_tier"] == "LOW", j["events"][1]
    assert j["events"][1]["new_tier"] == "HIGH", j["events"][1]
    assert j["events"][1]["axis_name"] == "data_sensitivity", j["events"][1]

    # Unknown server: empty events list
    r2 = c.get("/servers/nobody/escalation-timeline")
    assert r2.status_code == 200, r2.text
    assert r2.json()["events"] == [], r2.json()

    print("PASS")
