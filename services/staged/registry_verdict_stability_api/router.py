from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_verdict_stability


router = APIRouter(prefix="/api")


class VerdictStabilityBucket(BaseModel):
    risk_tier: str
    verdict: str
    count: int
    avg_trust_score: float | None = None
    avg_confidence: float | None = None


class VerdictStabilityResponse(BaseModel):
    days: int
    min_scans: int
    total: int
    buckets: list[VerdictStabilityBucket]
    stable_servers: int
    unstable_servers: int


@router.get(
    "/registry/verdict-stability",
    response_model=VerdictStabilityResponse,
)
def registry_verdict_stability(
    days: int = Query(default=30, ge=1),
    min_scans: int = Query(default=1, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_verdict_stability(
        session,
        days=days,
        min_scans=min_scans,
    )


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc)
    rows = [
        {
            "server_id": "self-test-low-approved",
            "name": "Self test low approved",
            "registry_source": "self-test",
            "trust_score": 0.95,
            "verdict": "approved",
            "verdict_reasoning": "Low risk and approved verdict.",
            "confidence": 0.98,
            "last_assessed": now - timedelta(days=1),
            "first_seen": now - timedelta(days=10),
            "last_seen": now - timedelta(days=1),
            "last_scanned": now - timedelta(days=1),
            "scan_count": 5,
            "risk_tier": "low",
        },
        {
            "server_id": "self-test-medium-review",
            "name": "Self test medium review",
            "registry_source": "self-test",
            "trust_score": 0.65,
            "verdict": "review",
            "verdict_reasoning": "Medium risk requires review.",
            "confidence": 0.82,
            "last_assessed": now - timedelta(days=2),
            "first_seen": now - timedelta(days=20),
            "last_seen": now - timedelta(days=2),
            "last_scanned": now - timedelta(days=2),
            "scan_count": 3,
            "risk_tier": "medium",
        },
        {
            "server_id": "self-test-high-blocked",
            "name": "Self test high blocked",
            "registry_source": "self-test",
            "trust_score": 0.15,
            "verdict": "blocked",
            "verdict_reasoning": "High risk is blocked.",
            "confidence": 0.97,
            "last_assessed": now - timedelta(days=3),
            "first_seen": now - timedelta(days=30),
            "last_seen": now - timedelta(days=3),
            "last_scanned": now - timedelta(days=3),
            "scan_count": 8,
            "risk_tier": "high",
        },
        {
            "server_id": "self-test-low-blocked",
            "name": "Self test low blocked",
            "registry_source": "self-test",
            "trust_score": 0.35,
            "verdict": "blocked",
            "verdict_reasoning": "A blocked verdict for a low-risk server.",
            "confidence": 0.76,
            "last_assessed": now - timedelta(days=4),
            "first_seen": now - timedelta(days=40),
            "last_seen": now - timedelta(days=4),
            "last_scanned": now - timedelta(days=4),
            "scan_count": 4,
            "risk_tier": "low",
        },
    ]

    testing_session.add_all(McpServerRegistry(**row) for row in rows)
    testing_session.commit()

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/registry/verdict-stability",
                params={"days": 30, "min_scans": 1},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 4
            assert any(
                bucket["risk_tier"] == "low"
                and bucket["verdict"] == "approved"
                and bucket["count"] == 1
                for bucket in payload["buckets"]
            )
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()