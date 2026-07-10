"""server_risk_delta_timeline_api.py -- chronological risk tier timeline for a server.

Reads mcp_llm_axis_scores scored_at timestamps and computes risk_tier per scored_at row
to show when a server's risk profile changed tiers.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["risk-delta-timeline"])

RISK_TIER_THRESHOLDS = {
    "CRITICAL": 0.75,
    "HIGH": 0.55,
    "MEDIUM": 0.35,
    "LOW": 0.0,
}


def _calculate_risk_tier(p_top: Optional[float]) -> str:
    if p_top is None:
        return "UNKNOWN"
    if p_top >= RISK_TIER_THRESHOLDS["CRITICAL"]:
        return "CRITICAL"
    elif p_top >= RISK_TIER_THRESHOLDS["HIGH"]:
        return "HIGH"
    elif p_top >= RISK_TIER_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    else:
        return "LOW"


class TimelineEntry(BaseModel):
    scored_at: str
    risk_tier: str
    overall_risk_label: str
    axis_count: int


class TimelineResponse(BaseModel):
    server_id: str
    timeline: List[TimelineEntry]


def _scored_at_iso(val) -> str:
    if isinstance(val, datetime):
        return val.isoformat()
    if val:
        return str(val)
    return ""


@router.get("/servers/{server_id}/risk-delta-timeline", response_model=TimelineResponse)
def get_risk_delta_timeline(
    server_id: str,
    limit: int = 20,
    db: Session = Depends(get_session),
) -> TimelineResponse:
    """Chronological timeline of risk tier changes for a single server.

    Groups mcp_llm_axis_scores rows by scored_at timestamp and computes
    risk_tier + overall_risk_label (with trust-gating applied) for each snapshot.
    """
    limit = max(1, min(limit, 100))

    reg = db.get(McpServerRegistry, server_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    overall_rows = (
        db.execute(
            select(McpLlmAxisScore)
            .where(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name == "overall_risk",
            )
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(500)
        )
        .scalars()
        .all()
    )

    if not overall_rows:
        return TimelineResponse(server_id=server_id, timeline=[])

    scored_at_values: List[datetime] = []
    seen: set = set()
    for row in overall_rows:
        key = _scored_at_iso(row.scored_at)
        if key and key not in seen:
            scored_at_values.append(row.scored_at)
            seen.add(key)
            if len(scored_at_values) >= limit:
                break

    timeline: List[TimelineEntry] = []

    for scored_at_val in scored_at_values:
        iso = _scored_at_iso(scored_at_val) if scored_at_val else ""

        axis_rows = (
            db.execute(
                select(McpLlmAxisScore).where(
                    McpLlmAxisScore.server_id == server_id,
                    McpLlmAxisScore.scored_at == scored_at_val,
                )
            )
            .scalars()
            .all()
        )

        axis_count = len(axis_rows)
        labels = {r.axis_name: r.label for r in axis_rows if r.label}
        overall_label = labels.get("overall_risk", "UNKNOWN")

        p_top = None
        for r in axis_rows:
            if r.axis_name == "overall_risk":
                p_top = r.p_top
                break

        risk_tier = _calculate_risk_tier(p_top)

        gate = trust_gate(reg.url, reg.name, labels)
        published_overall = gate.get("published_overall_risk") or overall_label

        timeline.append(
            TimelineEntry(
                scored_at=iso,
                risk_tier=risk_tier,
                overall_risk_label=published_overall,
                axis_count=axis_count,
            )
        )

    return TimelineResponse(server_id=server_id, timeline=timeline)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    s.add(
        McpServerRegistry(
            server_id="srv1",
            name="Stripe MCP",
            url="https://github.com/stripe/agent-toolkit",
        )
    )

    from datetime import datetime, timezone

    t0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2024, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    for i, (ax, lbl) in enumerate(
        (
            ("overall_risk", "CRITICAL"),
            ("auth_strength", "STRONG"),
            ("capability_breadth", "BROAD"),
            ("data_sensitivity", "CRITICAL"),
            ("network_egress", "EXTERNAL"),
            ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "MODERATE"),
        ),
        start=1,
    ):
        s.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559_20240101",
                p_top=0.80,
                scored_at=t0,
            )
        )

    for i, (ax, lbl) in enumerate(
        (
            ("overall_risk", "HIGH"),
            ("auth_strength", "STRONG"),
            ("capability_breadth", "MODERATE"),
            ("data_sensitivity", "HIGH"),
            ("network_egress", "EXTERNAL"),
            ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "LOW"),
        ),
        start=100,
    ):
        s.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559_20240201",
                p_top=0.55,
                scored_at=t1,
            )
        )

    for i, (ax, lbl) in enumerate(
        (
            ("overall_risk", "HIGH"),
            ("auth_strength", "STRONG"),
            ("capability_breadth", "NARROW"),
            ("data_sensitivity", "LOW"),
            ("network_egress", "INTERNAL"),
            ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "LOW"),
        ),
        start=200,
    ):
        s.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559_20240301",
                p_top=0.20,
                scored_at=t2,
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

    r = c.get("/api/servers/srv1/risk-delta-timeline")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv1", j
    assert isinstance(j["timeline"], list), j
    assert len(j["timeline"]) == 3, j
    # Newest first
    assert j["timeline"][0]["scored_at"].startswith("2024-03-01"), j["timeline"][0]
    assert "risk_tier" in j["timeline"][0], j["timeline"][0]
    assert "overall_risk_label" in j["timeline"][0], j["timeline"][0]
    assert "axis_count" in j["timeline"][0], j["timeline"][0]
    assert j["timeline"][0]["axis_count"] == 7, j["timeline"][0]
    # Stripe is verified -> capped to MEDIUM
    assert j["timeline"][0]["overall_risk_label"] == "MEDIUM", j["timeline"][0]
    assert j["timeline"][0]["risk_tier"] == "LOW", j["timeline"][0]
    assert j["timeline"][1]["risk_tier"] == "HIGH", j["timeline"][1]
    assert j["timeline"][2]["risk_tier"] == "CRITICAL", j["timeline"][2]

    r2 = c.get("/api/servers/srv1/risk-delta-timeline?limit=2")
    assert r2.status_code == 200, r2.text
    assert len(r2.json()["timeline"]) == 2, r2.json()

    r3 = c.get("/api/servers/nope/risk-delta-timeline")
    assert r3.status_code == 404, r3.text

    print("PASS")
