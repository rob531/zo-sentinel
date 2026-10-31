from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


class DisputeBacklogItem(BaseModel):
    id: int
    server_id: str
    server_name: Optional[str]
    submitted_by: str
    proposed_overall_risk: Optional[str]
    reason_category: Optional[str]
    status: str
    age_days: float


class DisputeBacklogResponse(BaseModel):
    count: int
    items: list[DisputeBacklogItem]
    by_status: dict[str, int]


class DisputeSummaryResponse(BaseModel):
    count: int = Field(ge=0)
    by_status: dict[str, int]
    by_reason_category: dict[str, int]


def _age_days(created_at: datetime) -> float:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created_at).total_seconds() / 86400.0


def _fetch_disputes(
    session: Session,
    status: Optional[str] = None,
    limit: int = 50,
) -> tuple[list[DisputeBacklogItem], dict[str, int]]:
    statement = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpServerRegistry.name.label("server_name"),
            McpScoreDispute.submitted_by,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .order_by(McpScoreDispute.created_at.asc(), McpScoreDispute.id.asc())
    )

    if status is None:
        statement = statement.where(McpScoreDispute.status != "resolved")
    else:
        statement = statement.where(McpScoreDispute.status == status)

    bounded_limit = max(1, min(int(limit), 1000))
    rows = session.execute(statement.limit(bounded_limit)).all()

    items = [
        DisputeBacklogItem(
            id=row.id,
            server_id=str(row.server_id),
            server_name=row.server_name,
            submitted_by=row.submitted_by,
            proposed_overall_risk=row.proposed_overall_risk,
            reason_category=row.reason_category,
            status=row.status,
            age_days=_age_days(row.created_at),
        )
        for row in rows
    ]
    by_status = dict(Counter(item.status for item in items))
    return items, by_status


def list_disputes(
    session: Session = Depends(get_session),
    status: Optional[str] = None,
    limit: int = 50,
) -> DisputeBacklogResponse:
    items, by_status = _fetch_disputes(session, status=status, limit=limit)
    return DisputeBacklogResponse(
        count=len(items),
        items=items,
        by_status=by_status,
    )


def get_dispute_summary(
    session: Session = Depends(get_session),
) -> DisputeSummaryResponse:
    status_counts = session.execute(
        select(McpScoreDispute.status, func.count(McpScoreDispute.id))
        .group_by(McpScoreDispute.status)
    ).all()
    reason_counts = session.execute(
        select(McpScoreDispute.reason_category, func.count(McpScoreDispute.id))
        .group_by(McpScoreDispute.reason_category)
    ).all()

    by_status = {str(status): int(count) for status, count in status_counts}
    by_reason_category = {
        str(reason): int(count)
        for reason, count in reason_counts
        if reason is not None
    }
    return DisputeSummaryResponse(
        count=sum(by_status.values()),
        by_status=by_status,
        by_reason_category=by_reason_category,
    )


get_dispute_backlog = list_disputes
get_backlog = list_disputes
get_summary = get_dispute_summary

router = APIRouter(prefix="/api/disputes")


@router.get("/backlog", response_model=DisputeBacklogResponse)
def dispute_backlog(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> DisputeBacklogResponse:
    items, by_status = _fetch_disputes(session, status=status, limit=limit)
    return DisputeBacklogResponse(
        count=len(items),
        items=items,
        by_status=by_status,
    )


@router.get("/backlog/summary", response_model=DisputeSummaryResponse)
def dispute_backlog_summary(
    session: Session = Depends(get_session),
) -> DisputeSummaryResponse:
    return get_dispute_summary(session=session)


app = FastAPI(title="dispute_backlog_queue")
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(bind=engine)
    McpScoreDispute.__table__.create(bind=engine)

    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-alpha",
                    name="Alpha Server",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="srv-beta",
                    name="Beta Server",
                    risk_tier="low",
                ),
            ]
        )
        session.add_all(
            [
                McpScoreDispute(
                    server_id="srv-alpha",
                    submitted_by="analyst-one",
                    proposed_overall_risk="high",
                    reason_category="model_disagreement",
                    explanation="The score appears overstated.",
                    status="pending",
                ),
                McpScoreDispute(
                    server_id="srv-beta",
                    submitted_by="analyst-two",
                    proposed_overall_risk="medium",
                    reason_category="missing_context",
                    explanation="Registry context was omitted.",
                    status="pending",
                ),
                McpScoreDispute(
                    server_id="srv-alpha",
                    submitted_by="analyst-three",
                    proposed_overall_risk="low",
                    reason_category="model_disagreement",
                    explanation="The score appears overstated.",
                    status="resolved",
                    resolved_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        pending_response = client.get(
            "/api/disputes/backlog",
            params={"status": "pending", "limit": 50},
        )
        assert pending_response.status_code == 200
        pending = pending_response.json()
        assert pending["count"] == 2
        assert len(pending["items"]) == 2
        assert any(item["server_name"] == "Alpha Server" for item in pending["items"])
        assert pending["by_status"]["pending"] == 2

        summary_response = client.get("/api/disputes/backlog/summary")
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert "pending" in summary["by_status"]
        assert summary["by_status"]["pending"] == 2
        assert summary["by_status"]["resolved"] == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()