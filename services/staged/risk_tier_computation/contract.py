"""
services/staged/risk_tier_computation/contract.py

FastAPI contract for the risk_tier_computation service.
Mirrors the exemplar contract implementation and provides a self‑test that can be
run with:

    python -m services.staged.risk_tier_computation.contract
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Real data layer – must be used in production code
from app.db import get_session
from app.models import McpLlmAxisScore, Base  # Base provides metadata for table creation

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Pydantic response model
# --------------------------------------------------------------------------- #
from pydantic import BaseModel


class RiskTierResponse(BaseModel):
    server_id: int
    risk_tier: str
    composite_score: float
    axis_summary: Dict[str, str]
    override_applied: bool


# --------------------------------------------------------------------------- #
# Helper: map composite score to tier string
# --------------------------------------------------------------------------- #
def _map_score_to_tier(score: float) -> str:
    if score > 75:
        return "TRUSTED_GENERAL"
    if score > 60:
        return "TRUSTED_RESEARCH"
    if score > 45:
        return "ENTERPRISE_CONTROLLED"
    if score > 30:
        return "CAUTION_LIMITED"
    if score > 15:
        return "HIGH_RISK_ISOLATED"
    return "HIGH_RISK_ISOLATED"


# --------------------------------------------------------------------------- #
# Endpoint implementation
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/risk_tier",
    response_model=RiskTierResponse,
    name="risk_tier_computation:get_risk_tier",
)
def get_risk_tier(
    server_id: int,
    db: Session = Depends(get_session),
) -> RiskTierResponse:
    # ------------------------------------------------------------------- #
    # Retrieve all axis scores for the server
    # ------------------------------------------------------------------- #
    stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    rows = db.execute(stmt).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found or no scores")

    # ------------------------------------------------------------------- #
    # Determine dominant label per axis (largest p_top)
    # ------------------------------------------------------------------- #
    axis_best: Dict[str, McpLlmAxisScore] = {}
    for row in rows:
        cur = axis_best.get(row.axis_name)
        if cur is None or (row.p_top or 0) > (cur.p_top or 0):
            axis_best[row.axis_name] = row

    # ------------------------------------------------------------------- #
    # Build summary and compute composite score (average of p_top * 100)
    # ------------------------------------------------------------------- #
    axis_summary: Dict[str, str] = {}
    total = 0.0
    for axis, best in axis_best.items():
        axis_summary[axis] = best.label
        total += (best.p_top or 0) * 100

    composite_score = total / len(axis_best) if axis_best else 0.0
    risk_tier = _map_score_to_tier(composite_score)

    # ------------------------------------------------------------------- #
    # Override handling – not present in schema, so always False
    # ------------------------------------------------------------------- #
    override_applied = False

    return RiskTierResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        composite_score=round(composite_score, 2),
        axis_summary=axis_summary,
        override_applied=override_applied,
    )


# --------------------------------------------------------------------------- #
# Self‑test (acceptance)
# --------------------------------------------------------------------------- #
def _create_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # In‑memory SQLite session override
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    app.dependency_overrides[get_session] = get_test_session
    return app


def _seed_test_data(db: Session) -> None:
    # Server 1 – high score -> TRUSTED_GENERAL
    db.add(
        McpLlmAxisScore(
            adapter_sha256="a1",
            axis_name="axis1",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=1,
            label="low",
            label_index=0,
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.80,
            probs="{}",
            scored_at=datetime.utcnow(),
            server_id=1,
        )
    )
    # Server 2 – medium score -> TRUSTED_RESEARCH
    db.add(
        McpLlmAxisScore(
            adapter_sha256="a2",
            axis_name="axis1",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=2,
            label="medium",
            label_index=1,
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.65,
            probs="{}",
            scored_at=datetime.utcnow(),
            server_id=2,
        )
    )
    # Server 3 – lower score -> ENTERPRISE_CONTROLLED
    db.add(
        McpLlmAxisScore(
            adapter_sha256="a3",
            axis_name="axis1",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=3,
            label="high",
            label_index=2,
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.50,
            probs="{}",
            scored_at=datetime.utcnow(),
            server_id=3,
        )
    )
    db.commit()


def _run_self_test() -> None:
    app = _create_test_app()
    client = TestClient(app)

    # Seed data
    with app.dependency_overrides[get_session]() as sess:
        _seed_test_data(sess)

    # Define expectations
    expectations = {
        1: "TRUSTED_GENERAL",
        2: "TRUSTED_RESEARCH",
        3: "ENTERPRISE_CONTROLLED",
    }

    for server_id, expected_tier in expectations.items():
        resp = client.get(f"/api/servers/{server_id}/risk_tier")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code} for server {server_id}"
        data = resp.json()
        assert data["risk_tier"] == expected_tier, f"Server {server_id} tier mismatch"
        assert isinstance(data["composite_score"], float)
        assert isinstance(data["axis_summary"], dict)

    print("PASS")


if __name__ == "__main__":
    _run_self_test()
    sys.exit(0)