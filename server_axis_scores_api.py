"""server_axis_scores_api.py -- per-server axis-scores endpoint.

GET /servers/{server_id}/axis-scores reads the 7 risk axes + overall_risk from
mcp_llm_axis_scores for a given server_id, returning axis_name, label,
label_index, p_top, p_critical, p_danger, probs per row sorted by label_index.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
Mirrors verdict_breakdown_api.py: real app.db / app.models imports, SQLAlchemy
select queries, no inline stubs.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/servers", tags=["servers"])


# ---- Pydantic response models ----

class AxisScoreOut(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    probs: Optional[dict] = None

    model_config = {"from_attributes": True}


class ServerAxisScoresResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[str] = None
    axes: list[AxisScoreOut]


# ---- Helpers ----

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


# ---- Endpoint ----

@router.get("/{server_id}/axis-scores", response_model=ServerAxisScoresResponse)
def get_server_axis_scores(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerAxisScoresResponse:
    """Return the 7 risk-axis rows for a given server_id, sorted by label_index.

    Raises 404 if the server has no axis scores.
    """
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore)
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
        .order_by(McpLlmAxisScore.label_index)
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No axis scores for server_id {server_id!r}")

    reg = db.get(McpServerRegistry, server_id)
    ref = rows[0]
    scored_at_iso = ref.scored_at.isoformat() if ref.scored_at else None

    axes_out = [
        AxisScoreOut(
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            probs=r.probs,
        )
        for r in rows
    ]

    return ServerAxisScoresResponse(
        server_id=server_id,
        name=reg.name if reg else None,
        url=reg.url if reg else None,
        model_version=mv,
        scored_at=scored_at_iso,
        axes=axes_out,
    )


# ---- Self-test (in-memory SQLite, no real DB) ----

if __name__ == "__main__":
    from datetime import datetime, timezone

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

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)

    # Seed: two servers, each with all 7 axes (unique per server_id+axis_name+model_version)
    sess = TS()
    now = datetime.now(timezone.utc)

    # server "test-srv" -- 7 rows (v3.0_40974559), then 1 extra overall_risk row
    # with a distinct model_version to satisfy UniqueConstraint(server_id,axis_name,model_version)
    for i, (ax, lbl, idx) in enumerate(
        [
            ("overall_risk", "HIGH", 3),
            ("auth_strength", "STRONG", 0),
            ("capability_breadth", "BROAD", 2),
            ("data_sensitivity", "CRITICAL", 4),
            ("network_egress", "EXTERNAL", 3),
            ("maintainer_trust", "ESTABLISHED", 1),
            ("exploit_surface", "MODERATE", 2),
        ],
        start=1,
    ):
        sess.add(
            McpLlmAxisScore(
                id=i,
                server_id="test-srv",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
                label_index=idx,
                p_top=0.20,
                p_critical=0.30,
                p_danger=0.45,
                probs={"CRITICAL": 0.45, "HIGH": 0.30, "MEDIUM": 0.15, "LOW": 0.10},
                scored_at=now,
            )
        )
    # 8th row: overall_risk with a different model_version so
    # UniqueConstraint(server_id, axis_name, model_version) is not violated
    sess.add(
        McpLlmAxisScore(
            id=8,
            server_id="test-srv",
            axis_name="overall_risk",
            label="CRITICAL",
            model_version="v3.0_40974559_extra",
            label_index=4,
            p_top=0.10,
            p_critical=0.55,
            p_danger=0.30,
            probs={"CRITICAL": 0.55, "HIGH": 0.30, "MEDIUM": 0.10, "LOW": 0.05},
            scored_at=now,
        )
    )

    # registry entry for test-srv
    sess.add(
        McpServerRegistry(
            server_id="test-srv",
            name="TestServer",
            url="https://example.com/test",
        )
    )

    # server "test-srv-2" -- 7 rows
    for i, (ax, lbl, idx) in enumerate(
        [
            ("overall_risk", "LOW", 0),
            ("auth_strength", "WEAK", 3),
            ("capability_breadth", "NARROW", 0),
            ("data_sensitivity", "LOW", 0),
            ("network_egress", "CONTROLLED", 0),
            ("maintainer_trust", "UNKNOWN", 2),
            ("exploit_surface", "SMALL", 0),
        ],
        start=101,
    ):
        sess.add(
            McpLlmAxisScore(
                id=i,
                server_id="test-srv-2",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
                label_index=idx,
                p_top=0.80,
                p_critical=0.05,
                p_danger=0.05,
                probs={"LOW": 0.80, "MEDIUM": 0.15, "HIGH": 0.05},
                scored_at=now,
            )
        )

    sess.commit()
    sess.close()

    # Test 1: happy path -- returns 7 axis rows for test-srv with p_top + probs fields
    resp = client.get("/servers/test-srv/axis-scores")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data, dict), f"Expected dict, got {type(data)}"
    assert len(data["axes"]) == 7, f"Expected 7 axes, got {len(data['axes'])}: {data['axes']}"
    for axis in data["axes"]:
        assert "p_top" in axis, f"Missing p_top in axis: {axis}"
        assert "probs" in axis, f"Missing probs in axis: {axis}"
        assert "axis_name" in axis
        assert "label" in axis
    assert data["server_id"] == "test-srv"
    assert data["name"] == "TestServer"
    assert data["model_version"] == "v3.0_40974559"
    assert data["scored_at"] is not None

    # Test 2: axes sorted by label_index
    label_indices = [ax["label_index"] for ax in data["axes"]]
    assert label_indices == sorted(label_indices), f"Expected sorted label_index, got {label_indices}"

    # Test 3: second server with 7 axes
    resp2 = client.get("/servers/test-srv-2/axis-scores")
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert len(data2["axes"]) == 7, f"Expected 7 axes for srv2, got {len(data2['axes'])}"
    for axis in data2["axes"]:
        assert "p_top" in axis
        assert "probs" in axis

    # Test 4: unknown server returns 404
    resp3 = client.get("/servers/nonexistent/axis-scores")
    assert resp3.status_code == 404, f"Expected 404, got {resp3.status_code}"

    # Test 5: empty server_id edge case (FastAPI path param validation)
    resp4 = client.get("/servers//axis-scores")
    assert resp4.status_code in (404, 405), f"Expected 404/405 for empty server_id, got {resp4.status_code}"

    print("PASS")
