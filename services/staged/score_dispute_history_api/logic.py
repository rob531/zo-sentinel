from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


class ScoreDisputeItem(BaseModel):
    id: int
    submitted_by: Optional[str] = None
    proposed_overall_risk: Optional[str] = None
    proposed_axes: Optional[Union[Dict[str, Any], List[Any]]] = None
    reason_category: Optional[str] = None
    explanation: Optional[str] = None
    status: Optional[str] = None
    admin_note: Optional[str] = None
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None


class ScoreDisputeHistoryResponse(BaseModel):
    server_id: str
    status_filter: str
    count: int
    disputes: List[ScoreDisputeItem]


def _datetime_to_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def build_service_score_dispute_history_api(
    server_id: str,
    status: str = "all",
    session: Session = Depends(get_session),
) -> ScoreDisputeHistoryResponse:
    if status not in {"open", "resolved", "all"}:
        raise HTTPException(
            status_code=422,
            detail="status must be one of: open, resolved, all",
        )

    statement = select(McpScoreDispute).where(
        McpScoreDispute.server_id == server_id
    )
    if status != "all":
        statement = statement.where(McpScoreDispute.status == status)

    statement = statement.order_by(
        McpScoreDispute.created_at.asc(),
        McpScoreDispute.id.asc(),
    )
    rows = session.execute(statement).scalars().all()

    disputes = [
        ScoreDisputeItem(
            id=row.id,
            submitted_by=row.submitted_by,
            proposed_overall_risk=row.proposed_overall_risk,
            proposed_axes=row.proposed_axes,
            reason_category=row.reason_category,
            explanation=row.explanation,
            status=row.status,
            admin_note=row.admin_note,
            created_at=_datetime_to_string(row.created_at),
            resolved_at=_datetime_to_string(row.resolved_at),
        )
        for row in rows
    ]

    return ScoreDisputeHistoryResponse(
        server_id=str(server_id),
        status_filter=status,
        count=len(disputes),
        disputes=disputes,
    )


def create_score_dispute_history_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    router.add_api_route(
        "/disputes/{server_id}",
        build_service_score_dispute_history_api,
        methods=["GET"],
        response_model=ScoreDisputeHistoryResponse,
    )
    return router


def _run_self_test() -> None:
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
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    open_axes = {
        "tool_poisoning": 0.91,
        "prompt_injection": 0.77,
    }
    resolved_axes = {
        "tool_poisoning": 0.42,
    }

    with testing_session() as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id="srv-1",
                    submitted_by="analyst@example.test",
                    proposed_overall_risk="high",
                    proposed_axes=open_axes,
                    reason_category="model_disagreement",
                    explanation="The score omitted prompt-injection evidence.",
                    status="open",
                    admin_note=None,
                    created_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id="srv-1",
                    submitted_by="reviewer@example.test",
                    proposed_overall_risk="medium",
                    proposed_axes=resolved_axes,
                    reason_category="missing_signal",
                    explanation="A previously omitted signal was added.",
                    status="resolved",
                    admin_note="Score adjusted after review.",
                    created_at=datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc),
                    resolved_at=datetime(2025, 1, 3, 12, 0, tzinfo=timezone.utc),
                ),
                McpScoreDispute(
                    server_id="srv-2",
                    submitted_by="other@example.test",
                    proposed_overall_risk="low",
                    proposed_axes={"permissions": 0.2},
                    reason_category="model_disagreement",
                    explanation="A dispute for another server.",
                    status="open",
                    admin_note=None,
                    created_at=datetime(2025, 1, 4, 12, 0, tzinfo=timezone.utc),
                    resolved_at=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(create_score_dispute_history_router())

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/srv-1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["server_id"] == "srv-1"
        assert payload["status_filter"] == "all"
        assert payload["count"] == 2
        assert len(payload["disputes"]) == 2

        open_response = client.get("/api/disputes/srv-1", params={"status": "open"})
        assert open_response.status_code == 200, open_response.text
        open_payload = open_response.json()
        assert open_payload["status_filter"] == "open"
        assert open_payload["count"] == 1
        assert len(open_payload["disputes"]) == 1
        assert open_payload["disputes"][0]["status"] == "open"
        assert open_payload["disputes"][0]["proposed_axes"] == open_axes

    print("PASS")


if __name__ == "__main__":
    _run_self_test()