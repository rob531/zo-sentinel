from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


class ReasonCategoryCount(BaseModel):
    reason_category: str
    count: int


class DisputeStatusCount(BaseModel):
    status: str
    count: int


class DisputeCategoryMixResponse(BaseModel):
    total: int
    by_category: List[ReasonCategoryCount]
    by_status: List[DisputeStatusCount]
    oldest_open_days: int


router = APIRouter(prefix="/api", tags=["disputes"])


def _oldest_open_days(created_at: Optional[datetime]) -> int:
    if created_at is None:
        return 0

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    elapsed = datetime.now(timezone.utc) - created_at
    return max(0, elapsed.days)


@router.get(
    "/disputes/reason-category-mix",
    response_model=DisputeCategoryMixResponse,
)
def get_dispute_reason_category_mix(
    status: Optional[str] = None,
    session: Session = Depends(get_session),
) -> DisputeCategoryMixResponse:
    filters = []
    if status is not None:
        filters.append(McpScoreDispute.status == status)

    category_query = (
        select(
            McpScoreDispute.reason_category,
            func.count(McpScoreDispute.id).label("count"),
        )
        .group_by(McpScoreDispute.reason_category)
        .order_by(McpScoreDispute.reason_category)
    )
    status_query = (
        select(
            McpScoreDispute.status,
            func.count(McpScoreDispute.id).label("count"),
        )
        .group_by(McpScoreDispute.status)
        .order_by(McpScoreDispute.status)
    )
    total_query = select(func.count(McpScoreDispute.id))
    oldest_query = select(func.min(McpScoreDispute.created_at)).where(
        McpScoreDispute.status != "resolved"
    )

    if filters:
        category_query = category_query.where(*filters)
        status_query = status_query.where(*filters)
        total_query = total_query.where(*filters)
        oldest_query = oldest_query.where(*filters)

    category_rows = session.execute(category_query).all()
    status_rows = session.execute(status_query).all()
    total = int(session.execute(total_query).scalar_one())
    oldest_created_at = session.execute(oldest_query).scalar_one_or_none()

    return DisputeCategoryMixResponse(
        total=total,
        by_category=[
            ReasonCategoryCount(reason_category=row[0], count=int(row[1]))
            for row in category_rows
        ],
        by_status=[
            DisputeStatusCount(status=row[0], count=int(row[1]))
            for row in status_rows
        ],
        oldest_open_days=_oldest_open_days(oldest_created_at),
    )


def run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    reason_category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes
                    (id, reason_category, status, created_at)
                VALUES
                    (1, 'policy_mismatch', 'open', :oldest),
                    (2, 'policy_mismatch', 'open', :recent),
                    (3, 'data_quality', 'resolved', :resolved_at),
                    (4, 'scoring_error', 'resolved', :resolved_at)
                """
            ),
            {
                "oldest": datetime.now(timezone.utc).replace(tzinfo=None),
                "recent": datetime.now(timezone.utc).replace(tzinfo=None),
                "resolved_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
        )

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/reason-category-mix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert {
            "reason_category": "policy_mismatch",
            "count": 2,
        } in payload["by_category"]
        assert {"status": "open", "count": 2} in payload["by_status"]

    print("PASS")


if __name__ == "__main__":
    run_self_test()