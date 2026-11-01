from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

try:
    from .logic import get_dispute_resolution_sla
except ImportError:
    from logic import get_dispute_resolution_sla


router = APIRouter(prefix="/api")


class SLABucket(BaseModel):
    reason_category: str
    open: int
    resolved: int
    under_24h: int
    d1_to_7d: int
    over_7d: int


class SLAOverall(BaseModel):
    under_24h: int
    d1_to_7d: int
    over_7d: int


class DisputeResolutionSLAResponse(BaseModel):
    days: int
    generated_at: datetime
    total: int
    open_count: int
    resolved_count: int
    median_hours: Optional[float]
    buckets: List[SLABucket]
    overall: SLAOverall


@router.get(
    "/disputes/sla",
    response_model=DisputeResolutionSLAResponse,
    status_code=200,
)
def dispute_resolution_sla(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> DisputeResolutionSLAResponse:
    return get_dispute_resolution_sla(session=session, days=days)


def _run_self_test() -> None:
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session() -> Session:
        with testing_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id="self-test-server",
                    submitted_by="self-test-user",
                    proposed_overall_risk=0.25,
                    proposed_axes={"trust": 0.25},
                    explanation="Resolved quickly",
                    reason_category="scoring",
                    status="resolved",
                    admin_note="Resolved",
                    created_at=now - timedelta(days=1),
                    resolved_at=now - timedelta(days=1) + timedelta(hours=2),
                ),
                McpScoreDispute(
                    server_id="self-test-server",
                    submitted_by="self-test-user",
                    proposed_overall_risk=0.50,
                    proposed_axes={"trust": 0.50},
                    explanation="Resolved after five days",
                    reason_category="scoring",
                    status="resolved",
                    admin_note="Resolved",
                    created_at=now - timedelta(days=6),
                    resolved_at=now - timedelta(days=1),
                ),
                McpScoreDispute(
                    server_id="self-test-server",
                    submitted_by="self-test-user",
                    proposed_overall_risk=0.75,
                    proposed_axes={"trust": 0.75},
                    explanation="Still open",
                    reason_category="scoring",
                    status="open",
                    admin_note=None,
                    created_at=now - timedelta(hours=3),
                    resolved_at=None,
                ),
            ]
        )
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/sla", params={"days": 30})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3, payload
    assert payload["open_count"] == 1, payload
    assert payload["resolved_count"] == 2, payload
    assert payload["median_hours"] is not None and payload["median_hours"] > 0, payload
    assert payload["overall"]["under_24h"] == 1, payload
    assert payload["overall"]["d1_to_7d"] == 1, payload
    assert payload["overall"]["over_7d"] == 0, payload
    assert any(
        bucket["reason_category"] == "scoring" and bucket["under_24h"] == 1
        for bucket in payload["buckets"]
    ), payload
    print("PASS")


if __name__ == "__main__":
    _run_self_test()