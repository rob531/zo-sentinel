# deps: fastapi, pydantic
"""Scoring criteria API -- exposes static risk-axis definitions to scoring consumers."""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/scoring", tags=["scoring"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

AXIS_LABELS: Dict[str, List[str]] = {
    "overall_risk": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL"],
    "auth_strength": ["STRONG", "MODERATE", "WEAK", "NONE"],
    "capability_breadth": ["BROAD", "MODERATE", "NARROW"],
    "data_sensitivity": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
    "network_egress": ["EXTERNAL", "INTERNAL", "NONE"],
    "maintainer_trust": ["ESTABLISHED", "VERIFIED", "UNKNOWN", "UNVERIFIED"],
    "exploit_surface": ["CRITICAL", "HIGH", "MODERATE", "LOW"],
}

CRITERIA_VERSION = "v3.0_40974559"
DECISION_RULE_VERSION = "rules_v2.1"


class AxisDefinition(BaseModel):
    axis_name: str
    labels: List[str]
    decision_rule_version: str


class ScoringCriteriaResponse(BaseModel):
    criteria_version: str
    axes: List[AxisDefinition]


@router.get("/criteria", response_model=ScoringCriteriaResponse)
def get_scoring_criteria(db: Session = Depends(get_session)) -> ScoringCriteriaResponse:
    """Return static definitions for all 7 risk axes consumed by the scoring pipeline."""
    axes = [
        AxisDefinition(
            axis_name=ax,
            labels=AXIS_LABELS[ax],
            decision_rule_version=DECISION_RULE_VERSION,
        )
        for ax in AXES
    ]
    return ScoringCriteriaResponse(
        criteria_version=CRITERIA_VERSION,
        axes=axes,
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

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    r = c.get("/scoring/criteria")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["criteria_version"], "criteria_version must be non-empty"
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}"
    assert j["axes"][0]["axis_name"] == "overall_risk", "overall_risk must be first"
    for ax in j["axes"]:
        assert "labels" in ax and ax["labels"], f"Missing labels for {ax['axis_name']}"
        assert "decision_rule_version" in ax
    assert r.json()["criteria_version"] == "v3.0_40974559"
    print("PASS")
