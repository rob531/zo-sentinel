# deps: fastapi, pydantic, sqlalchemy
"""
Ladder Rung Convergence Report

Computes per-axis mean and population standard deviation of p_top scores
for a given scoring wave (model_version), grouped by axis_name.
"""
from __future__ import annotations

import os
import sys as _sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in _sys.path:
    _sys.path.insert(0, _root)

from collections import defaultdict
from statistics import mean, pstdev

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["ladder_rung_convergence_report"])


class AxisStats(BaseModel):
    mean: float = Field(..., description="Mean of p_top scores for the axis")
    stddev: float = Field(..., description="Population standard deviation of p_top scores for the axis")


class ConvergenceReport(BaseModel):
    wave: str = Field(..., description="Scoring wave (model_version)")
    axes: dict[str, AxisStats] = Field(..., description="Axis name → statistics mapping")


@router.get(
    "/scorer/convergence",
    response_model=ConvergenceReport,
    summary="Axis-wise convergence statistics for a scoring wave",
)
def convergence_endpoint(
    wave: str = Query(..., description="Scoring wave identifier (matches McpLlmAxisScore.model_version)"),
    session: Session = Depends(get_session),
) -> ConvergenceReport:
    """
    Return per-axis mean and population standard deviation of p_top scores
    for all servers scored in the given wave (model_version).
    """
    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.model_version == wave)
        .all()
    )
    if not scores:
        raise HTTPException(status_code=404, detail=f"No scores found for wave {wave}")

    grouped: dict[str, list[float]] = defaultdict(list)
    for s in scores:
        if s.p_top is not None:
            grouped[s.axis_name].append(float(s.p_top))

    axes_stats: dict[str, AxisStats] = {}
    for axis, values in grouped.items():
        if len(values) == 1:
            std = 0.0
        else:
            std = pstdev(values)
        axes_stats[axis] = AxisStats(mean=round(mean(values), 6), stddev=round(std, 6))

    return ConvergenceReport(wave=wave, axes=axes_stats)


if __name__ == "__main__":
    import sys as _sys_local
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base, McpServerRegistry, McpLlmAxisScore

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    def override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed: 2 waves, 2 servers each, 2 axes with varying p_top to ensure stddev > 0
    with SessionLocal() as db:
        srv1 = McpServerRegistry(
            server_id="srv-1",
            name="server-one",
            registry_source="test",
            risk_tier="low",
            verdict="unknown",
            confidence=0.5,
            description="",
            first_seen=None,
            last_assessed=None,
            last_scanned=None,
            last_seen=None,
            meta="{}",
            scan_count=0,
            trust_score=0.0,
            url="http://example.com/1",
            verdict_reasoning="",
        )
        srv2 = McpServerRegistry(
            server_id="srv-2",
            name="server-two",
            registry_source="test",
            risk_tier="low",
            verdict="unknown",
            confidence=0.5,
            description="",
            first_seen=None,
            last_assessed=None,
            last_scanned=None,
            last_seen=None,
            meta="{}",
            scan_count=0,
            trust_score=0.0,
            url="http://example.com/2",
            verdict_reasoning="",
        )
        db.add_all([srv1, srv2])
        db.flush()

        # Wave "v1": security axis p_top varies (srv1=0.8, srv2=0.6 -> stddev>0)
        #            perf axis p_top is constant (stddev=0)
        for srv, sec_ptop, perf_ptop in [
            (srv1.server_id, 0.8, 0.5),
            (srv2.server_id, 0.6, 0.5),
        ]:
            for axis, ptop in [("security", sec_ptop), ("performance", perf_ptop)]:
                db.add(
                    McpLlmAxisScore(
                        server_id=srv,
                        axis_name=axis,
                        model_version="v1",
                        label="test",
                        label_index=0,
                        p_top=ptop,
                        p_critical=None,
                        p_danger=None,
                        probs=None,
                        escalated=False,
                        escalated_to=None,
                        decision_rule_version="v1",
                        adapter_sha256="deadbeef",
                        scored_at=None,
                    )
                )
        db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    resp = client.get("/api/scorer/convergence", params={"wave": "v1"})
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}")
        _sys_local.exit(1)

    data = resp.json()
    if data.get("wave") != "v1":
        print("FAIL: wave mismatch")
        _sys_local.exit(1)

    axes = data.get("axes", {})
    sec_std = axes.get("security", {}).get("stddev")
    perf_std = axes.get("performance", {}).get("stddev")

    if sec_std is None or sec_std <= 0:
        print(f"FAIL: security stddev not > 0: {sec_std}")
        _sys_local.exit(1)
    if perf_std != 0.0:
        print(f"FAIL: performance stddev not 0: {perf_std}")
        _sys_local.exit(1)

    # Test 404 for unknown wave
    resp404 = client.get("/api/scorer/convergence", params={"wave": "nonexistent"})
    if resp404.status_code != 404:
        print(f"FAIL: expected 404 for unknown wave, got {resp404.status_code}")
        _sys_local.exit(1)

    print("PASS")
    _sys_local.exit(0)
