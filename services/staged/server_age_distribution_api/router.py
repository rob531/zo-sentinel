from fastapi import APIRouter, Depends
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Dict, List

from app.db import get_session
from app.models import McpServerRegistry

from .logic import compute_age_distribution

router = APIRouter(prefix="/api", tags=["server_age_distribution"])


class CohortTiers(BaseModel):
    label: str
    count: int
    tiers: Dict[str, int]


class CohortsResponse(BaseModel):
    cohorts: List[CohortTiers]
    total_servers: int
    generated_at: datetime


@router.get("/servers/age-distribution", response_model=CohortsResponse)
async def get_server_age_distribution(session=Depends(get_session)):
    return compute_age_distribution(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    from datetime import timedelta
    from fastapi.testclient import TestClient

    now = datetime.now(timezone.utc)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    McpServerRegistry.__table__.create(engine)

    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    test_session.add(McpServerRegistry(
        name="s1", server_id="srv1", first_seen=now - timedelta(days=3),
        risk_tier="TRUSTED_GENERAL", registry_source="test"
    ))
    test_session.add(McpServerRegistry(
        name="s2", server_id="srv2", first_seen=now - timedelta(days=15),
        risk_tier="ENTERPRISE_CONTROLLED", registry_source="test"
    ))
    test_session.add(McpServerRegistry(
        name="s3", server_id="srv3", first_seen=now - timedelta(days=45),
        risk_tier="HIGH_RISK_ISOLATED", registry_source="test"
    ))
    test_session.add(McpServerRegistry(
        name="s4", server_id="srv4", first_seen=now - timedelta(days=60),
        risk_tier="CAUTION_LIMITED", registry_source="test"
    ))
    test_session.add(McpServerRegistry(
        name="s5", server_id="srv5", first_seen=now - timedelta(days=100),
        risk_tier="TRUSTED_RESEARCH", registry_source="test"
    ))
    test_session.add(McpServerRegistry(
        name="s6", server_id="srv6", first_seen=now - timedelta(days=120),
        risk_tier="ENTERPRISE_CONTROLLED", registry_source="test"
    ))
    test_session.add(McpServerRegistry(
        name="s7", server_id="srv7", first_seen=now - timedelta(days=150),
        risk_tier="CAUTION_LIMITED", registry_source="test"
    ))
    test_session.add(McpServerRegistry(
        name="s8", server_id="srv8", first_seen=now - timedelta(days=200),
        risk_tier="KNOWN_THREAT", registry_source="test"
    ))
    test_session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session() -> Session:
        return test_session

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/servers/age-distribution")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    cohort_labels = [c["label"] for c in data["cohorts"]]
    cohort_counts = {c["label"]: c["count"] for c in data["cohorts"]}

    expected_labels = ["0-7d", "7-30d", "30-90d", "90-180d", "180d+"]
    assert cohort_labels == expected_labels, f"Labels mismatch: {cohort_labels}"
    assert cohort_counts["0-7d"] == 1, f"0-7d: expected 1, got {cohort_counts['0-7d']}"
    assert cohort_counts["7-30d"] == 1, f"7-30d: expected 1, got {cohort_counts['7-30d']}"
    assert cohort_counts["30-90d"] == 2, f"30-90d: expected 2, got {cohort_counts['30-90d']}"
    assert cohort_counts["90-180d"] == 3, f"90-180d: expected 3, got {cohort_counts['90-180d']}"
    assert cohort_counts["180d+"] == 1, f"180d+: expected 1, got {cohort_counts['180d+']}"
    assert data["total_servers"] == 8, f"total_servers: expected 8, got {data['total_servers']}"

    print("PASS")