"""HTTP surface for the server evidence bundle service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_evidence_bundle as load_evidence_bundle

router = APIRouter(prefix="/api", tags=["server-evidence-bundle"])


class AxisEvidence(BaseModel):
    label: str | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated: bool | None = None
    escalated_to: str | None = None
    scored_at: datetime | None = None


class ThreatSample(BaseModel):
    indicator_type: str
    indicator_value: str
    source: str | None = None
    fetched_at: datetime | None = None


class ThreatEvidence(BaseModel):
    count: int
    sample: list[ThreatSample] = []


class DisputeEvidence(BaseModel):
    open_count: int
    latest_status: str | None = None


class EvidenceBundleResponse(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    confidence: float | None = None
    last_assessed: datetime | None = None
    axes: dict[str, AxisEvidence]
    threats: ThreatEvidence
    disputes: DisputeEvidence
    criteria_versions: list[str]


@router.get(
    "/servers/{server_id}/evidence-bundle",
    response_model=EvidenceBundleResponse,
)
def get_server_evidence_bundle(
    server_id: str,
    db: Session = Depends(get_session),
) -> EvidenceBundleResponse:
    evidence = load_evidence_bundle(db, server_id)
    return EvidenceBundleResponse.model_validate(evidence)


def _self_test() -> None:
    from fastapi import FastAPI

    axis_names = [
        "capability",
        "security",
        "reliability",
        "performance",
        "observability",
        "maintainability",
        "compliance",
    ]
    scored_at = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    payload: dict[str, Any] = {
        "server_id": "server-self-test",
        "name": "Self-test server",
        "verdict": "conditional",
        "risk_tier": "medium",
        "trust_score": 0.75,
        "confidence": 0.9,
        "last_assessed": scored_at,
        "axes": {
            name: {
                "label": name,
                "p_top": 0.5,
                "p_critical": 0.2,
                "p_danger": 0.3,
                "escalated": False,
                "escalated_to": None,
                "scored_at": scored_at,
            }
            for name in axis_names
        },
        "threats": {
            "count": 1,
            "sample": [
                {
                    "indicator_type": "hostname",
                    "indicator_value": "self-test.invalid",
                    "source": "self-test",
                    "fetched_at": scored_at,
                }
            ],
        },
        "disputes": {"open_count": 1, "latest_status": "open"},
        "criteria_versions": ["rule-v1"],
    }

    original_loader = load_evidence_bundle

    def fake_loader(db: Session, server_id: str) -> dict[str, Any]:
        return payload

    globals()["load_evidence_bundle"] = fake_loader
    try:
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[get_session] = lambda: None
        with TestClient(application) as client:
            response = client.get(
                "/api/servers/server-self-test/evidence-bundle"
            )
        assert response.status_code == 200
        body = response.json()
        assert set(body["axes"]) == set(axis_names)
        assert body["threats"]["count"] == 1
        assert body["disputes"]["open_count"] == 1
        assert body["criteria_versions"]
    finally:
        globals()["load_evidence_bundle"] = original_loader

    print("PASS")


if __name__ == "__main__":
    _self_test()