from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import Dict, Any

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk"])


class TierData(BaseModel):
    threshold: float
    count: int


class ThresholdsResponse(BaseModel):
    thresholds: Dict[str, TierData]
    coverage: Dict[str, float]


def get_tier_threshold(tier: str) -> float:
    tier_thresholds = {
        "critical": 80.0,
        "high": 60.0,
        "medium": 40.0,
        "low": 20.0,
        "none": 0.0,
    }
    return tier_thresholds.get(tier.lower(), 0.0)


def compute_risk_calibration(db: Session) -> ThresholdsResponse:
    results = db.execute(
        text("""
            SELECT 
                risk_tier,
                COUNT(*) as server_count
            FROM McpServerRegistry
            WHERE risk_tier IS NOT NULL
            GROUP BY risk_tier
        """)
    ).fetchall()

    thresholds = {}
    coverage = {}
    total_servers = sum(row[1] for row in results)

    tier_order = ["critical", "high", "medium", "low", "none"]

    for tier in tier_order:
        tier_count = next((row[1] for row in results if row[0] == tier), 0)
        thresholds[tier] = TierData(
            threshold=get_tier_threshold(tier),
            count=tier_count
        )
        if total_servers > 0:
            coverage[tier] = round((tier_count / total_servers) * 100, 2)
        else:
            coverage[tier] = 0.0

    return ThresholdsResponse(thresholds=thresholds, coverage=coverage)


@router.get("/risk/calibration", response_model=ThresholdsResponse)
def get_calibration(session: Session = Depends(get_session)) -> ThresholdsResponse:
    return compute_risk_calibration(session)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    SessionLocal = sessionmaker(bind=in_memory_engine)
    test_session = SessionLocal()

    test_session.execute(
        text("""
            CREATE TABLE IF NOT EXISTS McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                description TEXT,
                meta TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER
            )
        """)
    )

    from datetime import datetime, timedelta

    day1 = datetime.now() - timedelta(days=2)
    day2 = datetime.now() - timedelta(days=1)

    servers = [
        ("srv-001", "Alpha Service", "high", 75.0, day1.isoformat(), day2.isoformat()),
        ("srv-002", "Beta Service", "high", 65.0, day1.isoformat(), day2.isoformat()),
        ("srv-003", "Gamma Service", "critical", 85.0, day2.isoformat(), day2.isoformat()),
    ]

    for srv in servers:
        test_session.execute(
            text("""
                INSERT INTO McpServerRegistry 
                (server_id, name, risk_tier, trust_score, first_seen, last_seen)
                VALUES (:srv_id, :name, :tier, :score, :first_seen, :last_seen)
            """),
            {"srv_id": srv[0], "name": srv[1], "tier": srv[2], "score": srv[3],
             "first_seen": srv[4], "last_seen": srv[5]}
        )

    test_session.commit()

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/risk/calibration")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    thresholds = data["thresholds"]

    assert len(thresholds) > 0, "Expected non-empty thresholds"

    critical_count = thresholds.get("critical", {}).get("count", 0)
    assert critical_count == 1, f"Expected critical count 1, got {critical_count}"

    print("PASS")