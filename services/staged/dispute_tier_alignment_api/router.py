"""Thin HTTP surface for dispute tier alignment."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

from .logic import get_dispute_tier_alignment as calculate_dispute_tier_alignment

router = APIRouter(prefix="/api", tags=["disputes"])


class DisputeStatusCounts(BaseModel):
    open: int
    accepted: int
    rejected: int
    resolved: int
    other: int


class TierAlignmentCounts(BaseModel):
    aligned: int
    changed: int
    unknown: int


class DisputeTierAlignmentServer(BaseModel):
    server_id: str
    status: str
    proposed_overall_risk: str
    current_risk_tier: str | None
    aligned: bool
    days_open: int
    created_at: str | None
    resolved_at: str | None


class DisputeTierAlignmentResponse(BaseModel):
    total_disputes: int
    by_status: DisputeStatusCounts
    alignment: TierAlignmentCounts
    servers: list[DisputeTierAlignmentServer]
    aligned_count: int
    changed_count: int
    unknown_count: int


@router.get(
    "/disputes/tier-alignment",
    response_model=DisputeTierAlignmentResponse,
)
def get_dispute_tier_alignment_endpoint(
    db: Session = Depends(get_session),
) -> DisputeTierAlignmentResponse:
    return DisputeTierAlignmentResponse(**calculate_dispute_tier_alignment(db))


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="aligned-server",
                    name="Aligned server",
                    risk_tier="high",
                    trust_score=80,
                    verdict="trusted",
                ),
                McpServerRegistry(
                    server_id="changed-server",
                    name="Changed server",
                    risk_tier="low",
                    trust_score=30,
                    verdict="review",
                ),
            ]
        )
        session.commit()
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="aligned-server",
                    submitted_by="self-test",
                    proposed_overall_risk="high",
                    proposed_axes=None,
                    reason_category="tier",
                    explanation="Matching tier",
                    status="open",
                ),
                McpScoreDispute(
                    id=2,
                    server_id="changed-server",
                    submitted_by="self-test",
                    proposed_overall_risk="critical",
                    proposed_axes=None,
                    reason_category="tier",
                    explanation="Different tier",
                    status="resolved",
                    resolved_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpScoreDispute(
                    id=3,
                    server_id="missing-server",
                    submitted_by="self-test",
                    proposed_overall_risk="medium",
                    proposed_axes=None,
                    reason_category="tier",
                    explanation="Missing registry row",
                    status="open",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/tier-alignment")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_disputes"] == 3, payload
        assert payload["aligned_count"] == 1, payload
        assert payload["changed_count"] == 1, payload
        assert payload["unknown_count"] == 1, payload
        assert payload["by_status"]["open"] == 2, payload

    engine.dispose()
    print("PASS")