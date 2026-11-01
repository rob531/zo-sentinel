from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

try:
    from .logic import get_score_dispute_resolution_latency
except ImportError:
    from .logic import get_resolution_latency as get_score_dispute_resolution_latency


router = APIRouter(prefix="/api", tags=["disputes"])


class ResolutionLatencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    days: int
    generated_at: datetime
    by_status: dict[str, int]
    by_reason_category: dict[str, int]
    resolved_count: int
    unresolved_count: int
    median_resolution_hours: float | None
    p90_resolution_hours: float | None


@router.get(
    "/disputes/resolution-latency",
    response_model=ResolutionLatencyResponse,
)
def score_dispute_resolution_latency(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
):
    return get_score_dispute_resolution_latency(session, days)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        McpScoreDispute(
            server_id="server-1",
            submitted_by="user-1",
            proposed_overall_risk=0.8,
            explanation="First dispute",
            reason_category="scoring",
            status="open",
            created_at=now - timedelta(days=2),
        ),
        McpScoreDispute(
            server_id="server-2",
            submitted_by="user-2",
            proposed_overall_risk=0.7,
            explanation="Second dispute",
            reason_category="scoring",
            status="open",
            created_at=now - timedelta(days=1),
        ),
        McpScoreDispute(
            server_id="server-3",
            submitted_by="user-3",
            proposed_overall_risk=0.6,
            explanation="Third dispute",
            reason_category="data_quality",
            status="in_review",
            created_at=now - timedelta(days=1),
        ),
        McpScoreDispute(
            server_id="server-4",
            submitted_by="user-4",
            proposed_overall_risk=0.5,
            explanation="Fourth dispute",
            reason_category="scoring",
            status="resolved",
            created_at=now - timedelta(days=2),
            resolved_at=now - timedelta(days=2) + timedelta(hours=1),
        ),
        McpScoreDispute(
            server_id="server-5",
            submitted_by="user-5",
            proposed_overall_risk=0.4,
            explanation="Fifth dispute",
            reason_category="data_quality",
            status="resolved",
            created_at=now - timedelta(days=3),
            resolved_at=now - timedelta(days=3) + timedelta(hours=49),
        ),
    ]
    testing_session.add_all(rows)
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: testing_session

    try:
        from fastapi.testclient import TestClient

        with TestClient(test_app) as client:
            response = client.get("/api/disputes/resolution-latency?days=30")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["resolved_count"] == 2
            assert payload["median_resolution_hours"] == 25.0
            assert payload["unresolved_count"] == 3
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()