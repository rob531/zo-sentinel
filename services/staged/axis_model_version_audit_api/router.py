"""router.py -- the service's HTTP surface (one concern, one file).

Thin wrapper exposing `router`. Injects the real Session via Depends(get_session)
and delegates to the service's logic module.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_model_version_audit

router = APIRouter(prefix="/api", tags=["axis_model_version_audit"])


class Combination(BaseModel):
    model_version: str
    adapter_sha256: str
    decision_rule_version: str
    servers: int
    latest_scored_at: datetime
    mixed: bool


class AxisInfo(BaseModel):
    axis: str
    combinations: List[Combination]
    distinct_combinations: int = Field(..., ge=0)


class AxisModelVersionAuditResponse(BaseModel):
    axes: List[AxisInfo]


@router.get(
    "/axis-model-versions",
    response_model=AxisModelVersionAuditResponse,
    summary="Audit model version usage per axis",
)
def get_axis_model_versions(db: Session = Depends(get_session)) -> AxisModelVersionAuditResponse:
    """Delegate to the logic layer and return the audit payload."""
    payload = axis_model_version_audit(db)
    return AxisModelVersionAuditResponse(**payload)


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore, McpServerRegistry

    # Create an in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # All models share the same MetaData instance
    McpLlmAxisScore.metadata.create_all(engine)
    McpServerRegistry.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Session:
        return SessionLocal()

    # Build a minimal FastAPI app for the test
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with SessionLocal() as db:
        # Servers
        db.add_all(
            [
                McpServerRegistry(
                    server_id="srv-1",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    risk_tier="medium",
                ),
            ]
        )
        # Axis scores (3 rows, 2 axes, 2 model versions)
        now = datetime.utcnow()
        db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="srv-1",
                    axis_name="overall_risk",
                    model_version="v1.0",
                    adapter_sha256="a1"*32,
                    decision_rule_version="dr1",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="srv-2",
                    axis_name="overall_risk",
                    model_version="v2.0",
                    adapter_sha256="a2"*32,
                    decision_rule_version="dr1",
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="srv-1",
                    axis_name="auth_strength",
                    model_version="v1.0",
                    adapter_sha256="a1"*32,
                    decision_rule_version="dr2",
                    scored_at=now,
                ),
            ]
        )
        db.commit()

    client = TestClient(app)

    resp = client.get("/api/axis-model-versions")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    axes = data.get("axes", [])
    assert len(axes) == 2, f"Expected 2 axes, got {len(axes)}"

    legal_axes = {
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    }

    found_two_combination_axis = False
    for axis_entry in axes:
        axis_name = axis_entry.get("axis")
        assert axis_name in legal_axes, f"Illegal axis name {axis_name}"
        distinct = axis_entry.get("distinct_combinations", -1)
        if distinct == 2:
            found_two_combination_axis = True

    assert found_two_combination_axis, "No axis reported distinct_combinations == 2"
    print("PASS")
    sys.exit(0)