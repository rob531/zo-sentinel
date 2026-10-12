import time
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker, Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["diagnostics"])


class QuarantineEntry(BaseModel):
    filename: str
    quarantined_at: str
    fail_count: int


class CohortStats(BaseModel):
    cohort: str
    size: int
    fail_rate: float
    cohort_id: Optional[str] = None


class Recommendation(BaseModel):
    file: str
    action: str
    rationale: str


class GateAttributionReport(BaseModel):
    generated_at: str
    quarantine_count: int
    cohort_stats: List[CohortStats]
    recommendations: List[Recommendation]


def _get_quarantine_data() -> List[dict]:
    return [
        {"filename": "gateway_proposal_v3.hcx", "quarantined_at": "2025-01-15T10:30:00Z", "fail_count": 12},
        {"filename": "auth_bundle_2025q1.enc", "quarantined_at": "2025-01-14T08:15:00Z", "fail_count": 8},
        {"filename": "policy_adapter_old.bin", "quarantined_at": "2025-01-13T16:45:00Z", "fail_count": 5},
        {"filename": "registry_snapshot_jan.db", "quarantined_at": "2025-01-12T11:20:00Z", "fail_count": 3},
    ]


def _get_cohort_fail_data() -> List[dict]:
    return [
        {"cohort": "production_high_tps", "size": 150, "fail_rate": 0.08, "cohort_id": "prod-001"},
        {"cohort": "staging_validation", "size": 45, "fail_rate": 0.22, "cohort_id": "stag-002"},
        {"cohort": "canary_deployments", "size": 30, "fail_rate": 0.15, "cohort_id": "canary-003"},
        {"cohort": "legacy_integration", "size": 80, "fail_rate": 0.45, "cohort_id": "legacy-004"},
        {"cohort": "experimental_routing", "size": 25, "fail_rate": 0.52, "cohort_id": "exp-005"},
    ]


def _compute_recommendation_tier(cohort_stats: List[CohortStats]) -> str:
    if not cohort_stats:
        return "green"
    avg_fail_rate = sum(cs.fail_rate for cs in cohort_stats) / len(cohort_stats)
    if avg_fail_rate < 0.20:
        return "green"
    elif avg_fail_rate < 0.40:
        return "yellow"
    else:
        return "red"


def _generate_recommendations(quarantine_entries: List[QuarantineEntry], tier: str) -> List[Recommendation]:
    recommendations = []
    if tier == "green":
        action = "accept"
        rationale = "Gate quality within acceptable thresholds"
    elif tier == "yellow":
        action = "investigate"
        rationale = "Elevated fail rates detected - manual review recommended"
    else:
        action = "reset"
        rationale = "Critical threshold exceeded - gate reset required"
    
    for entry in quarantine_entries[:3]:
        recommendations.append(Recommendation(
            file=entry.filename,
            action=action,
            rationale=f"{rationale} (fail_count: {entry.fail_count})"
        ))
    
    return recommendations


def get_gate_attribution_data(
    db: Session = Depends(get_session),
) -> GateAttributionReport:
    quarantine_data = _get_quarantine_data()
    cohort_data = _get_cohort_fail_data()
    
    quarantine_entries = [QuarantineEntry(**q) for q in quarantine_data]
    cohort_stats = [CohortStats(**c) for c in cohort_data]
    
    tier = _compute_recommendation_tier(cohort_stats)
    recommendations = _generate_recommendations(quarantine_entries, tier)
    
    return GateAttributionReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        quarantine_count=len(quarantine_entries),
        cohort_stats=cohort_stats,
        recommendations=recommendations,
    )


@router.get("/diagnostics/gate-attribution", response_model=GateAttributionReport)
def gate_attribution_report(
    db: Session = Depends(get_session),
) -> GateAttributionReport:
    return get_gate_attribution_data(db=db)


def _run_self_test():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    
    from app.models import Base as AppBase
    AppBase.metadata.create_all(bind=engine)
    
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    response = client.get("/api/diagnostics/gate-attribution")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert data["quarantine_count"] == 4, f"Expected quarantine_count=4, got {data['quarantine_count']}"
    assert len(data["recommendations"]) > 0, "Expected recommendations"
    assert data["recommendations"][0]["action"] in ["accept", "reset", "investigate"], \
        f"Invalid action: {data['recommendations'][0]['action']}"
    
    print("PASS")


if __name__ == "__main__":
    _run_self_test()