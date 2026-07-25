# deps: fastapi, pydantic, sqlalchemy, requests
"""GET /probes/tiers/calibration -- compares risk tier thresholds with the
distribution of mcp_llm_axis_scores and suggests calibration adjustments.

Reads p_top distributions per label tier from mcp_llm_axis_scores and returns
suggested threshold adjustments for LOW/MEDIUM/HIGH/CRITICAL."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/probes", tags=["probe"])


def _percentile(sorted_vals: List[float], p: float) -> float:
    """Linear interpolation percentile; p in [0, 100]."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return sorted_vals[-1]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _distribution_stats(db: Session, label: str, axis: str = "overall_risk") -> dict:
    """Return min/p25/p50/p75/max of p_top for a given label."""
    rows = db.execute(
        select(McpLlmAxisScore.p_top).where(
            McpLlmAxisScore.axis_name == axis,
            McpLlmAxisScore.label == label,
            McpLlmAxisScore.p_top.isnot(None),
        )
    ).scalars().all()
    if not rows:
        return {"count": 0, "p25": None, "p50": None, "p75": None}
    vals = sorted(float(r) for r in rows)
    return {
        "count": len(vals),
        "min": vals[0],
        "p25": _percentile(vals, 25),
        "p50": _percentile(vals, 50),
        "p75": _percentile(vals, 75),
        "max": vals[-1],
    }


# Canonical risk-tier ordering
TIER_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Heuristic current boundaries (these are what the system uses today as defaults)
# The probe compares these against the observed distributions.
_CURRENT_BOUNDARIES = {
    "LOW": 0.75,
    "MEDIUM": 0.50,
    "HIGH": 0.25,
    "CRITICAL": 0.0,
}


class TierSuggestion(BaseModel):
    tier: str
    current_boundary: float
    suggested_boundary: float
    rationale: str


class CalibrationResponse(BaseModel):
    adjustments: List[TierSuggestion]
    distribution_summary: dict
    axis: str = "overall_risk"


@router.get("/tiers/calibration", response_model=CalibrationResponse)
def calibrate_tiers(db: Session = Depends(get_session)) -> CalibrationResponse:
    """Compare current risk-tier thresholds with the empirical p_top distribution
    per label tier and return suggested calibration adjustments."""
    axis = "overall_risk"

    # Gather distribution stats for each tier
    dists = {}
    for tier in TIER_ORDER:
        dists[tier] = _distribution_stats(db, tier, axis)

    total_servers = sum(d["count"] for d in dists.values())
    if total_servers == 0:
        raise HTTPException(status_code=404, detail="No scored servers found for overall_risk axis")

    adjustments: List[TierSuggestion] = []

    for i, tier in enumerate(TIER_ORDER):
        d = dists[tier]
        current = _CURRENT_BOUNDARIES[tier]

        if d["count"] == 0:
            adjustments.append(TierSuggestion(
                tier=tier,
                current_boundary=current,
                suggested_boundary=current,
                rationale="No servers labeled this tier; no adjustment possible.",
            ))
            continue

        # Suggest boundary = median of the tier below's p75 (or tier's own p25)
        # This creates separation between tiers.
        if i == 0:  # LOW: suggest p25 of LOW as boundary upward
            suggested = round(d["p25"], 4)
        elif i == len(TIER_ORDER) - 1:  # CRITICAL: no lower bound needed
            suggested = 0.0
        else:
            # Midpoint between this tier's p25 and the tier above's p75
            above = dists[TIER_ORDER[i - 1]]
            if above["count"] > 0 and d["p25"] is not None:
                suggested = round((above["p75"] + d["p25"]) / 2.0, 4)
            elif d["p50"] is not None:
                suggested = round(d["p50"], 4)
            else:
                suggested = current

        delta = suggested - current
        if abs(delta) < 0.005:
            rationale = "Distribution stable; no meaningful adjustment."
        elif delta > 0:
            rationale = (
                f"p_top distribution suggests boundary should shift +{delta:.3f} "
                f"(p25={d['p25']:.3f}, p50={d['p50']:.3f}, p75={d['p75']:.3f})."
            )
        else:
            rationale = (
                f"p_top distribution suggests boundary should shift {delta:.3f} "
                f"(p25={d['p25']:.3f}, p50={d['p50']:.3f}, p75={d['p75']:.3f})."
            )

        adjustments.append(TierSuggestion(
            tier=tier,
            current_boundary=round(current, 4),
            suggested_boundary=suggested,
            rationale=rationale,
        ))

    return CalibrationResponse(
        adjustments=adjustments,
        distribution_summary={
            tier: {"count": d["count"], "p25": d["p25"], "p50": d["p50"], "p75": d["p75"]}
            for tier, d in dists.items()
        },
        axis=axis,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                         poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    def seed(db: Session) -> None:
        # LOW servers: high p_top (model confident LOW)
        for i in range(3):
            db.add(McpLlmAxisScore(id=i + 1,   server_id=f"low_{i}",
                                   axis_name="overall_risk", label="LOW",
                                   p_top=0.85 + i * 0.03,
                                   model_version="v3.0_40974559"))
        # MEDIUM servers: mid p_top
        for i in range(4):
            db.add(McpLlmAxisScore(id=i + 10,  server_id=f"med_{i}",
                                   axis_name="overall_risk", label="MEDIUM",
                                   p_top=0.60 + i * 0.05,
                                   model_version="v3.0_40974559"))
        # HIGH servers: lower p_top
        for i in range(3):
            db.add(McpLlmAxisScore(id=i + 20,  server_id=f"high_{i}",
                                   axis_name="overall_risk", label="HIGH",
                                   p_top=0.40 + i * 0.04,
                                   model_version="v3.0_40974559"))
        # CRITICAL servers: low p_top
        for i in range(2):
            db.add(McpLlmAxisScore(id=i + 30,  server_id=f"crit_{i}",
                                   axis_name="overall_risk", label="CRITICAL",
                                   p_top=0.20 + i * 0.03,
                                   model_version="v3.0_40974559"))
        db.commit()

    seed(TS())

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

    # Happy path: endpoint returns non-empty suggestions
    r = c.get("/probes/tiers/calibration")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "adjustments" in j, j
    assert len(j["adjustments"]) == 4, j  # LOW/MEDIUM/HIGH/CRITICAL
    for tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        adj = next((a for a in j["adjustments"] if a["tier"] == tier), None)
        assert adj is not None, f"Missing tier {tier}"
        assert "current_boundary" in adj, adj
        assert "suggested_boundary" in adj, adj
    assert j["axis"] == "overall_risk", j
    assert "distribution_summary" in j, j

    # Edge case: empty score table
    eng2 = create_engine("sqlite://", connect_args={"check_same_thread": False},
                          poolclass=StaticPool)
    Base.metadata.create_all(eng2)
    TS2 = sessionmaker(bind=eng2, autoflush=False, autocommit=False)

    app2 = FastAPI()
    app2.include_router(router)

    def _override_empty():
        d = TS2()
        try:
            yield d
        finally:
            d.close()

    app2.dependency_overrides[get_session] = _override_empty
    c2 = TestClient(app2)
    r2 = c2.get("/probes/tiers/calibration")
    assert r2.status_code == 404, f"Expected 404 for empty table, got {r2.status_code}"

    print("PASS")
