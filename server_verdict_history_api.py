"""server_verdict_history_api.py -- historical verdict endpoint.

Exposes GET /servers/{server_id}/verdict/history returning all historical
scoring rounds for a server from mcp_llm_axis_scores, with the 7 axes per
round, overall score, risk tier, and timestamp.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/servers", tags=["verdict"])


class AxisPoint(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    scored_at: Optional[str] = None


class HistoryRound(BaseModel):
    model_version: Optional[str] = None
    axes: List[AxisPoint]
    overall_risk_score: Optional[float] = None
    risk_tier: Optional[str] = None


class VerdictHistory(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    verdict_reasoning: Optional[str] = None
    criteria_version: Optional[str] = None
    history: List[HistoryRound]


AXES = ("overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface")


def _derive_overall(score: float) -> float:
    return max(0.0, min(1.0, score))


def _tier(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score >= 0.80:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"


def _round(
    rows: List[McpLlmAxisScore],
    model_version: Optional[str],
    risk_tier_override: Optional[str] = None,
) -> HistoryRound:
    axes: List[AxisPoint] = []
    overall_raw: Optional[float] = None
    scored_at: Optional[str] = None

    for r in rows:
        if r.scored_at:
            scored_at = r.scored_at.isoformat() if hasattr(r.scored_at, "isoformat") else str(r.scored_at)
        if r.axis_name == "overall_risk" and r.p_top is not None:
            overall_raw = r.p_top
        axes.append(AxisPoint(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            scored_at=scored_at,
        ))

    overall_score = _derive_overall(overall_raw) if overall_raw is not None else None
    risk_tier = risk_tier_override or _tier(overall_score)
    return HistoryRound(
        model_version=model_version,
        axes=axes,
        overall_risk_score=overall_score,
        risk_tier=risk_tier,
    )


@router.get(
    "/{server_id}/verdict/history",
    response_model=VerdictHistory,
    status_code=status.HTTP_200_OK,
    summary="Retrieve historical verdict records for a server",
)
def get_verdict_history(
    server_id: str,
    db: Session = Depends(get_session),
) -> VerdictHistory:
    """Return all historical scoring rounds for the requested server.

    Rows are grouped by model_version and ordered by scored_at DESC so the
    most recent round appears first.  Axes include per-axis probabilities and
    the overall risk score + tier.
    """
    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None
    verdict_reasoning = reg.verdict_reasoning if reg else None

    rows = db.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
    ).scalars().all()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No scores found for server_id={server_id!r}",
        )

    # Group consecutive rows sharing the same model_version.
    history: List[HistoryRound] = []
    current_version: Optional[str] = None
    current_rows: List[McpLlmAxisScore] = []

    for r in rows:
        if r.model_version != current_version:
            if current_rows:
                # Apply trust-gating override to the overall_risk axis for this round.
                labels = {row.axis_name: row.label for row in current_rows if row.label}
                gate = trust_gate(url, name, labels)
                override = gate.get("published_overall_risk")
                tier = _tier(None)
                if override:
                    override_upper = override.upper()
                    if override_upper in ("CRITICAL", "HIGH"):
                        tier = "high"
                    elif override_upper in ("MEDIUM", "ELEVATED"):
                        tier = "medium"
                    else:
                        tier = "low"
                history.append(_round(current_rows, current_version, risk_tier_override=tier))
            current_version = r.model_version
            current_rows = []
        current_rows.append(r)

    if current_rows:
        labels = {row.axis_name: row.label for row in current_rows if row.label}
        gate = trust_gate(url, name, labels)
        override = gate.get("published_overall_risk")
        tier = _tier(None)
        if override:
            override_upper = override.upper()
            if override_upper in ("CRITICAL", "HIGH"):
                tier = "high"
            elif override_upper in ("MEDIUM", "ELEVATED"):
                tier = "medium"
            else:
                tier = "low"
        history.append(_round(current_rows, current_version, risk_tier_override=tier))

    criteria_version = rows[0].model_version if rows else None

    return VerdictHistory(
        server_id=server_id,
        name=name,
        url=url,
        verdict_reasoning=verdict_reasoning,
        criteria_version=criteria_version,
        history=history,
    )


if __name__ == "__main__":
    import sys
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

    from datetime import datetime, timezone

    def _mkdt(label: str) -> datetime:
        return datetime(2024, 1, 1, 12, 0, 0,
                        tzinfo=timezone.utc) if label == "round1" else datetime(2024, 2, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Seed two scoring rounds for srv1 (ordered desc by scored_at).
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            verdict_reasoning="Verified official publisher"))
    axes_r1 = (("overall_risk", "HIGH", 0.95, 0.05, 0.80),
               ("auth_strength", "STRONG", 0.85, 0.02, 0.10),
               ("capability_breadth", "BROAD", 0.90, 0.03, 0.15),
               ("data_sensitivity", "CRITICAL", 0.88, 0.04, 0.20),
               ("network_egress", "EXTERNAL", 0.75, 0.01, 0.05),
               ("maintainer_trust", "ESTABLISHED", 0.92, 0.01, 0.05),
               ("exploit_surface", "MODERATE", 0.60, 0.03, 0.15))
    for i, (ax, lbl, p1, p2, p3) in enumerate(axes_r1, start=1):
        s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559",
                              p_top=p1, p_critical=p2, p_danger=p3,
                              scored_at=datetime(2024, 2, 1, 12, 0, 0, tzinfo=timezone.utc)))
    axes_r2 = (("overall_risk", "HIGH", 0.90, 0.04, 0.75),
               ("auth_strength", "STRONG", 0.82, 0.02, 0.10),
               ("capability_breadth", "BROAD", 0.88, 0.03, 0.14),
               ("data_sensitivity", "CRITICAL", 0.85, 0.04, 0.18),
               ("network_egress", "EXTERNAL", 0.72, 0.01, 0.05),
               ("maintainer_trust", "ESTABLISHED", 0.90, 0.01, 0.05),
               ("exploit_surface", "MODERATE", 0.58, 0.03, 0.14))
    for i, (ax, lbl, p1, p2, p3) in enumerate(axes_r2, start=100):
        s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974560",
                              p_top=p1, p_critical=p2, p_danger=p3,
                              scored_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)))
    # srv2: single round
    s.add(McpServerRegistry(server_id="srv2", name="Unknown MCP", url="https://example.com/unknown"))
    axes_s2 = (("overall_risk", "CRITICAL", 0.98, 0.08, 0.90),
               ("auth_strength", "WEAK", 0.20, 0.05, 0.30),
               ("capability_breadth", "NARROW", 0.15, 0.02, 0.10),
               ("data_sensitivity", "HIGH", 0.80, 0.05, 0.30),
               ("network_egress", "EXTERNAL", 0.85, 0.02, 0.10),
               ("maintainer_trust", "UNKNOWN", 0.10, 0.01, 0.02),
               ("exploit_surface", "HIGH", 0.90, 0.10, 0.50))
    for i, (ax, lbl, p1, p2, p3) in enumerate(axes_s2, start=200):
        s.add(McpLlmAxisScore(id=i, server_id="srv2", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559",
                              p_top=p1, p_critical=p2, p_danger=p3,
                              scored_at=datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc)))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Happy path: srv1 has 2 scoring rounds
    r = c.get("/servers/srv1/verdict/history")
    if r.status_code != 200:
        print(f"FAIL: expected 200, got {r.status_code}: {r.text}")
        sys.exit(1)
    j = r.json()
    if j.get("server_id") != "srv1":
        print(f"FAIL: server_id mismatch: {j}")
        sys.exit(1)
    if len(j.get("history", [])) != 2:
        print(f"FAIL: expected 2 history rounds, got {len(j.get('history', []))}: {j}")
        sys.exit(1)
    # Latest round first
    latest = j["history"][0]
    if latest.get("criteria_version") is not None:
        print(f"FAIL: criteria_version should be named model_version in HistoryRound: {latest}")
        sys.exit(1)
    if latest.get("model_version") != "v3.0_40974559":
        print(f"FAIL: model_version mismatch: {latest}")
        sys.exit(1)
    # Must contain all 7 axes
    axis_names = {ax["axis_name"] for ax in latest.get("axes", [])}
    expected_axes = set(AXES)
    if axis_names != expected_axes:
        print(f"FAIL: expected axes {expected_axes}, got {axis_names}: {latest}")
        sys.exit(1)
    # srv2: single round
    r2 = c.get("/servers/srv2/verdict/history")
    if r2.status_code != 200:
        print(f"FAIL: srv2 expected 200, got {r2.status_code}: {r2.text}")
        sys.exit(1)
    j2 = r2.json()
    if len(j2.get("history", [])) != 1:
        print(f"FAIL: srv2 expected 1 round, got {len(j2.get('history', []))}: {j2}")
        sys.exit(1)
    # 404 for unknown server
    if c.get("/servers/nosuchserver/verdict/history").status_code != 404:
        print("FAIL: expected 404 for unknown server")
        sys.exit(1)

    print("PASS")
