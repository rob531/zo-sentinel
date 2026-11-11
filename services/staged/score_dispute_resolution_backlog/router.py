from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

from .logic import get_dispute_backlog


class DisputeBacklogResponse(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_reason: Dict[str, int]
    median_open_age_days: Optional[float]
    disputes: List[Dict[str, Any]]


router = APIRouter(prefix="/api")


@router.get("/disputes/backlog", response_model=DisputeBacklogResponse)
def get_backlog(
    status: str = Query("OPEN"),
    limit: int = Query(50, ge=1, le=500),
    session=Depends(get_session),
):
    return get_dispute_backlog(session, status=status, limit=limit)


def _run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    now = datetime.utcnow()
    session.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id="server-1",
                submitted_by="user-1",
                proposed_overall_risk=0.91,
                proposed_axes={"risk": 0.91},
                reason_category="model_error",
                explanation="Model disagreed with the assessment.",
                status="OPEN",
                created_at=now.replace(microsecond=0),
            ),
            McpScoreDispute(
                id=2,
                server_id="server-2",
                submitted_by="user-2",
                proposed_overall_risk=0.72,
                proposed_axes={"risk": 0.72},
                reason_category="policy_mismatch",
                explanation="Policy interpretation differs.",
                status="OPEN",
                created_at=now.replace(microsecond=0),
            ),
            McpScoreDispute(
                id=3,
                server_id="server-3",
                submitted_by="user-3",
                proposed_overall_risk=0.63,
                proposed_axes={"risk": 0.63},
                reason_category="model_error",
                explanation="Additional evidence was supplied.",
                status="OPEN",
                created_at=now.replace(microsecond=0),
            ),
            McpScoreDispute(
                id=4,
                server_id="server-4",
                submitted_by="user-4",
                proposed_overall_risk=0.48,
                proposed_axes={"risk": 0.48},
                reason_category="policy_mismatch",
                explanation="The dispute was reviewed.",
                status="RESOLVED",
                admin_note="Resolved after review.",
                created_at=now.replace(microsecond=0),
                resolved_at=now.replace(microsecond=0),
            ),
            McpScoreDispute(
                id=5,
                server_id="server-5",
                submitted_by="user-5",
                proposed_overall_risk=0.22,
                proposed_axes={"risk": 0.22},
                reason_category="model_error",
                explanation="The dispute was rejected.",
                status="REJECTED",
                admin_note="No disagreement was found.",
                created_at=now.replace(microsecond=0),
                resolved_at=now.replace(microsecond=0),
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        return session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/disputes/backlog",
                params={"status": "OPEN", "limit": 50},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["by_status"]["OPEN"] == 3
        assert payload["by_status"]["RESOLVED"] == 1
        assert len(payload["by_reason"]) == 2
        assert len(payload["disputes"]) == 5
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_contract()