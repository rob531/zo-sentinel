# deps: fastapi, pydantic, sqlalchemy, requests
"""trust_gate_decision_api.py -- expose trust_gating_override.trust_gate() as a REST endpoint.

PURPOSE: resolve the 'published_overall_risk' and 'trusted' derived axes for any server
by calling trust_gate(url, name, {axis_name: label}).

INTERFACE: GET /trust-gate/{server_id} with optional query param axes (comma-separated
axis names, default=all 7 risk axes).

DATA ACCESS: reads McpLlmAxisScore + McpServerRegistry via app.db SQLAlchemy session.
Writes health heartbeat to service_health via write_service HTTP.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

SERVICE_NAME = "trust_gate_decision_api"
HEALTH_INTERVAL = 60  # seconds

router = APIRouter(prefix="", tags=["trust_gate"])

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _heartbeat() -> None:
    """Write service health to write_service so the governor can detect a stalled service."""
    try:
        requests.post(
            "http://127.0.0.1:8772/write",
            json={
                "table": "service_health",
                "rows": {
                    "server_id": SERVICE_NAME,
                    "status": "healthy",
                    "meta": '{"service":"' + SERVICE_NAME + '","version":"1.0"}',
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                "wait": True,
            },
            timeout=5,
        )
    except Exception:
        pass


def _start_heartbeat() -> None:
    def loop():
        while True:
            _heartbeat()
            time.sleep(HEALTH_INTERVAL)

    t = threading.Thread(target=loop, daemon=True)
    t.start()


# Start heartbeat at module import
_start_heartbeat()


class AxisDecision(BaseModel):
    label: Optional[str] = None
    decision_rule_version: Optional[str] = None
    override_applied: bool = False
    raw_trust_score: Optional[float] = None


class OverallDecision(BaseModel):
    label: Optional[str] = None
    override_applied: bool = False
    raw_composite: Optional[str] = None


class TrustGateResponse(BaseModel):
    server_id: str
    decisions: Dict[str, AxisDecision]
    overall: OverallDecision
    trusted: bool
    evaluated_at: str


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = (
        db.execute(
            select(McpLlmAxisScore.model_version)
            .where(McpLlmAxisScore.server_id == server_id)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    return row


@router.get("/trust-gate/{server_id}", response_model=TrustGateResponse)
def get_trust_gate(
    server_id: str,
    axes: Optional[str] = None,
    db: Session = Depends(get_session),
) -> TrustGateResponse:
    """Resolve trust-gating decisions for one or all axes of *server_id*.

    axes: optional comma-separated axis names. Default = all 7 risk axes.
    """
    # Determine which axes to evaluate
    requested_axes: list[str]
    if axes:
        requested_axes = [a.strip() for a in axes.split(",") if a.strip() in AXES]
    else:
        requested_axes = list(AXES)

    # Check server exists in registry
    reg = db.get(McpServerRegistry, server_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    # Get latest model version for this server
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(
            status_code=404, detail=f"No axis scores found for server_id {server_id!r}"
        )

    # Fetch axis rows
    rows = (
        db.execute(
            select(McpLlmAxisScore).where(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.model_version == mv,
                McpLlmAxisScore.axis_name.in_(requested_axes),
            )
        )
        .scalars()
        .all()
    )

    # Build axis label map
    axis_labels: Dict[str, str] = {}
    row_map: Dict[str, McpLlmAxisScore] = {}
    for r in rows:
        axis_labels[r.axis_name] = r.label or ""
        row_map[r.axis_name] = r

    # Call trust_gate for the full record (handles overall_risk + maintainer_trust context)
    gate = trust_gate(reg.url, reg.name, axis_labels)

    # Build per-axis decision dict
    decisions: Dict[str, AxisDecision] = {}
    for ax in requested_axes:
        r = row_map.get(ax)
        raw_score = r.p_top if r else None
        override = False
        if ax == "overall_risk":
            override = bool(gate.get("capped")) and gate.get("published_overall_risk") != gate.get(
                "original_overall_risk"
            )
        decisions[ax] = AxisDecision(
            label=axis_labels.get(ax),
            decision_rule_version=r.decision_rule_version if r else None,
            override_applied=override,
            raw_trust_score=raw_score,
        )

    return TrustGateResponse(
        server_id=server_id,
        decisions=decisions,
        overall=OverallDecision(
            label=gate.get("published_overall_risk") or axis_labels.get("overall_risk"),
            override_applied=bool(gate.get("capped")),
            raw_composite=gate.get("original_overall_risk"),
        ),
        trusted=bool(gate.get("trusted")),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":  # CI-safe self-test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    from unittest.mock import patch

    # In-memory SQLite
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed server + axis rows using only required constructor kwargs
    s = SessionLocal()
    s.add(
        McpServerRegistry(
            server_id="known-server-1",
            name="Stripe MCP",
            url="https://github.com/stripe/agent-toolkit",
        )
    )
    for i, (ax, lbl) in enumerate(
        [
            ("overall_risk", "HIGH"),
            ("auth_strength", "STRONG"),
            ("capability_breadth", "BROAD"),
            ("data_sensitivity", "CRITICAL"),
            ("network_egress", "EXTERNAL"),
            ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "MODERATE"),
        ],
        start=1,
    ):
        s.add(
            McpLlmAxisScore(
                id=i,
                server_id="known-server-1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
            )
        )
    s.commit()
    s.close()

    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)

    # Disable heartbeat network call during test
    with patch("trust_gate_decision_api.requests.post"):
        app.dependency_overrides[get_session] = _override_session
        c = TestClient(app)

        # 404 on unknown server
        r = c.get("/trust-gate/unknown-server")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

        # Happy path: all axes
        r = c.get("/trust-gate/known-server-1")
        assert r.status_code == 200, r.text
        j = r.json()
        assert "trusted" in j, j
        assert isinstance(j["trusted"], bool), j
        assert "overall" in j, j
        assert "label" in j["overall"], j

        # Filtered to overall_risk axis
        r = c.get("/trust-gate/known-server-1?axes=overall_risk")
        assert r.status_code == 200, r.text
        j = r.json()
        assert "overall_risk" in j["decisions"], j
        assert j["overall"]["label"] in ("MEDIUM", "HIGH"), j  # MEDIUM if Stripe capped, HIGH otherwise

    print("PASS")
