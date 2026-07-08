"""server_axis_scores_bulk_api.py -- batch axis-scores endpoint.

Reads the 7 risk-axis rows (overall_risk, auth_strength, capability_breadth,
data_sensitivity, network_egress, maintainer_trust, exploit_surface) for a
batch of server_ids from mcp_llm_axis_scores.  Optional ?include_metadata=1
joins server name/verdict from mcp_server_registry.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
Mirrors verdict_breakdown_api.py: real app.db / app.models imports, SQLAlchemy
select queries, no inline stubs.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/servers", tags=["servers"])

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


# ---- Pydantic request/response models ----

class AxisScoreOut(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False


class ServerAxisScoresOut(BaseModel):
    server_id: str
    axes: list[AxisScoreOut]
    scored_at: str
    name: Optional[str] = None
    verdict: Optional[str] = None

    model_config = {"from_attributes": True}


class BulkRequest(BaseModel):
    server_ids: list[str]


class BulkResponse(BaseModel):
    servers: list[ServerAxisScoresOut]


# ---- Endpoint ----

@router.post("/axis-scores/bulk", response_model=BulkResponse)
def get_bulk_axis_scores(
    request: BulkRequest,
    include_metadata: bool = Query(False),
    db: Session = Depends(get_session),
) -> BulkResponse:
    """Return latest 7-axis scores for each server_id in the request body.

    - HTTP 400 if server_ids > 100
    - Unknown server_ids return an empty axes list
    - ?include_metadata=1 joins name/verdict from mcp_server_registry
    """
    if len(request.server_ids) > 100:
        raise HTTPException(status_code=400, detail="server_ids exceeds maximum of 100")

    if not request.server_ids:
        return BulkResponse(servers=[])

    # Fetch all latest scores for the requested servers in one query.
    # subquery: MAX(scored_at) per server_id
    latest_sub = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(McpLlmAxisScore.server_id.in_(request.server_ids))
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    stmt = (
        select(McpLlmAxisScore)
        .join(
            latest_sub,
            and_(
                McpLlmAxisScore.server_id == latest_sub.c.server_id,
                McpLlmAxisScore.scored_at == latest_sub.c.max_scored_at,
            ),
        )
        .where(McpLlmAxisScore.server_id.in_(request.server_ids))
    )
    rows = db.execute(stmt).scalars().all()

    # Group axis rows by server_id
    by_server: dict[str, list[McpLlmAxisScore]] = {}
    for r in rows:
        by_server.setdefault(r.server_id, []).append(r)

    # Optionally fetch registry metadata
    meta_map: dict[str, McpServerRegistry] = {}
    if include_metadata:
        regs = db.execute(
            select(McpServerRegistry).where(
                McpServerRegistry.server_id.in_(request.server_ids)
            )
        ).scalars().all()
        meta_map = {r.server_id: r for r in regs}

    servers_out: list[ServerAxisScoresOut] = []
    for sid in request.server_ids:
        axis_rows = by_server.get(sid, [])
        if not axis_rows:
            servers_out.append(
                ServerAxisScoresOut(server_id=sid, axes=[], scored_at="")
            )
            continue

        # All rows for one server share scored_at / model_version
        ref = axis_rows[0]
        scored_at_iso = ref.scored_at.isoformat() if ref.scored_at else ""

        axes_out = [
            AxisScoreOut(
                axis_name=r.axis_name,
                label=r.label,
                label_index=r.label_index,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                escalated=r.escalated or False,
            )
            for r in axis_rows
            if r.axis_name in AXES
        ]

        reg = meta_map.get(sid)
        servers_out.append(
            ServerAxisScoresOut(
                server_id=sid,
                axes=axes_out,
                scored_at=scored_at_iso,
                name=reg.name if reg else None,
                verdict=reg.verdict if reg else None,
            )
        )

    return BulkResponse(servers=servers_out)


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

    # Seed: two servers, each with all 7 axes
    sess = TS()
    now = datetime.now(timezone.utc)

    # server "srv1" -- 7 rows
    for i, ax in enumerate(AXES, start=1):
        sess.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv1",
                axis_name=ax,
                label="HIGH",
                model_version="v3.0_40974559",
                label_index=3,
                p_top=0.15,
                p_critical=0.35,
                p_danger=0.40,
                escalated=False,
                scored_at=now,
            )
        )

    # server "srv2" -- 7 rows
    for i, ax in enumerate(AXES, start=101):
        sess.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv2",
                axis_name=ax,
                label="LOW",
                model_version="v3.0_40974559",
                label_index=1,
                p_top=0.75,
                p_critical=0.10,
                p_danger=0.10,
                escalated=False,
                scored_at=now,
            )
        )

    # registry entry for srv2
    sess.add(
        McpServerRegistry(
            server_id="srv2",
            name="TrustedServer",
            verdict="approved",
            url="https://example.com/trusted",
        )
    )

    sess.commit()
    sess.close()

    # Test 1: bulk request for 2 known server_ids returns both with all 7 axes
    resp = client.post("/servers/axis-scores/bulk", json={"server_ids": ["srv1", "srv2"]})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert len(data["servers"]) == 2, f"Expected 2 servers, got {len(data['servers'])}"
    for srv in data["servers"]:
        assert len(srv["axes"]) == 7, (
            f"Expected 7 axes for {srv['server_id']}, got {len(srv['axes'])}"
        )
        for axis in srv["axes"]:
            assert "label" in axis
            assert "label_index" in axis
            assert "p_top" in axis
            assert "p_critical" in axis
            assert "p_danger" in axis

    # Test 2: bulk for 1 unknown returns empty axes
    resp = client.post("/servers/axis-scores/bulk", json={"server_ids": ["srv_unknown"]})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert len(data["servers"]) == 1, data
    assert data["servers"][0]["axes"] == [], data["servers"][0]
    assert data["servers"][0]["server_id"] == "srv_unknown"

    # Test 3: include_metadata=1 joins registry fields
    resp = client.post(
        "/servers/axis-scores/bulk",
        params={"include_metadata": "1"},
        json={"server_ids": ["srv2"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["servers"][0]["name"] == "TrustedServer", data["servers"][0]
    assert data["servers"][0]["verdict"] == "approved", data["servers"][0]

    # Test 4: bulk with >100 server_ids returns 400
    many_ids = [f"srv_{i:03d}" for i in range(101)]
    resp = client.post("/servers/axis-scores/bulk", json={"server_ids": many_ids})
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"

    # Test 5: empty server_ids returns empty list
    resp = client.post("/servers/axis-scores/bulk", json={"server_ids": []})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["servers"] == [], data

    print("PASS")
