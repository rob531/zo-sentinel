from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, distinct, func, literal_column, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


router = APIRouter(prefix="/api")


class ReasonMixCategory(BaseModel):
    reason_category: str
    disputes: int = Field(ge=0)
    open: int = Field(ge=0)
    raised_recent: int = Field(ge=0)
    mean_resolution_hours: Optional[float] = Field(default=None, ge=0)
    servers: int = Field(ge=0)


class ReasonMixResponse(BaseModel):
    window_days: int = 30
    categories: List[ReasonMixCategory]
    total_disputes: int = Field(ge=0)


@router.get("/disputes/reason-mix", response_model=ReasonMixResponse)
def get_dispute_reason_mix(
    session: Session = Depends(get_session),
) -> ReasonMixResponse:
    dialect = session.get_bind().dialect.name

    if dialect == "postgresql":
        cutoff = literal_column("CURRENT_TIMESTAMP - INTERVAL '30 days'")
        resolution_hours = literal_column(
            "EXTRACT(EPOCH FROM (resolved_at - created_at))/3600.0"
        )
    else:
        cutoff = func.datetime("now", "-30 days")
        resolution_hours = (
            func.julianday(McpScoreDispute.resolved_at)
            - func.julianday(McpScoreDispute.created_at)
        ) * 24.0

    statement = (
        select(
            McpScoreDispute.reason_category.label("reason_category"),
            func.count(McpScoreDispute.id).label("disputes"),
            func.sum(
                case(
                    (McpScoreDispute.status != "resolved", 1),
                    else_=0,
                )
            ).label("open"),
            func.sum(
                case(
                    (McpScoreDispute.created_at >= cutoff, 1),
                    else_=0,
                )
            ).label("raised_recent"),
            func.avg(
                case(
                    (
                        and_(
                            McpScoreDispute.status == "resolved",
                            McpScoreDispute.resolved_at.is_not(None),
                        ),
                        resolution_hours,
                    ),
                    else_=None,
                )
            ).label("mean_resolution_hours"),
            func.count(distinct(McpScoreDispute.server_id)).label("servers"),
        )
        .select_from(McpScoreDispute)
        .group_by(McpScoreDispute.reason_category)
        .order_by(McpScoreDispute.reason_category)
    )

    categories: List[ReasonMixCategory] = []
    total_disputes = 0

    for row in session.execute(statement):
        disputes = int(row.disputes or 0)
        total_disputes += disputes
        mean_resolution = row.mean_resolution_hours

        categories.append(
            ReasonMixCategory(
                reason_category=str(row.reason_category),
                disputes=disputes,
                open=int(row.open or 0),
                raised_recent=int(row.raised_recent or 0),
                mean_resolution_hours=(
                    None if mean_resolution is None else float(mean_resolution)
                ),
                servers=int(row.servers or 0),
            )
        )

    return ReasonMixResponse(
        window_days=30,
        categories=categories,
        total_disputes=total_disputes,
    )


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.utcnow()
    session = test_session_factory()
    try:
        session.add_all(
            [
                McpScoreDispute(
                    server_id="server-a",
                    submitted_by="user-a",
                    proposed_overall_risk="high",
                    proposed_axes={"security": 0.9},
                    reason_category="policy",
                    explanation="Policy disagreement",
                    status="resolved",
                    admin_note="Resolved",
                    created_at=now - timedelta(hours=48),
                    resolved_at=now,
                ),
                McpScoreDispute(
                    server_id="server-b",
                    submitted_by="user-b",
                    proposed_overall_risk="medium",
                    proposed_axes={"quality": 0.6},
                    reason_category="policy",
                    explanation="Awaiting review",
                    status="open",
                    admin_note=None,
                    created_at=now - timedelta(hours=2),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id="server-c",
                    submitted_by="user-c",
                    proposed_overall_risk="low",
                    proposed_axes={"security": 0.3},
                    reason_category="scoring",
                    explanation="Scoring disagreement",
                    status="resolved",
                    admin_note="Resolved",
                    created_at=now - timedelta(hours=24),
                    resolved_at=now,
                ),
                McpScoreDispute(
                    server_id="server-d",
                    submitted_by="user-d",
                    proposed_overall_risk="low",
                    proposed_axes={"quality": 0.2},
                    reason_category="scoring",
                    explanation="Scoring disagreement",
                    status="resolved",
                    admin_note="Resolved",
                    created_at=now - timedelta(hours=36),
                    resolved_at=now,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db_session = test_session_factory()
        try:
            yield db_session
        finally:
            db_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/reason-mix")

    assert response.status_code == 200, response.text
    payload = response.json()
    categories = {
        category["reason_category"]: category for category in payload["categories"]
    }

    assert payload["window_days"] == 30
    assert payload["total_disputes"] == 4
    assert set(categories) == {"policy", "scoring"}
    assert categories["policy"]["open"] == 1
    assert categories["policy"]["mean_resolution_hours"] == 48
    print("PASS")


if __name__ == "__main__":
    _self_test()