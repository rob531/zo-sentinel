from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_reason_category_mix

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


class ReasonCategoryStatusCounts(BaseModel):
    reason_category: str
    by_status: dict[str, int]
    open_count: int
    oldest_open_days: float


class ReasonCategoryMixResponse(BaseModel):
    categories: list[ReasonCategoryStatusCounts]
    total_open: int
    total: int


@router.get(
    "/reason-category-mix",
    response_model=ReasonCategoryMixResponse,
    summary="Get dispute counts by reason category and status",
)
def reason_category_mix(
    as_of: date = Query(..., description="Date used to calculate unresolved dispute ages"),
    session: Session = Depends(get_session),
) -> ReasonCategoryMixResponse:
    return ReasonCategoryMixResponse(
        **get_reason_category_mix(session, as_of)
    )


if __name__ == "__main__":
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session_factory() as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id=1,
                    submitted_by=1,
                    reason_category="model_disagreement",
                    explanation="Score differs from the submitted assessment.",
                    proposed_overall_risk=0.4,
                    proposed_axes={},
                    status="open",
                    created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                ),
                McpScoreDispute(
                    server_id=2,
                    submitted_by=1,
                    reason_category="missing_evidence",
                    explanation="Additional evidence is required.",
                    proposed_overall_risk=0.6,
                    proposed_axes={},
                    status="open",
                    created_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
                McpScoreDispute(
                    server_id=1,
                    submitted_by=2,
                    reason_category="model_disagreement",
                    explanation="The score was reviewed and corrected.",
                    proposed_overall_risk=0.7,
                    proposed_axes={},
                    status="resolved",
                    created_at=datetime(2024, 12, 20, tzinfo=timezone.utc),
                    resolved_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = testing_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/disputes/reason-category-mix",
            params={"as_of": "2025-01-10"},
        )
        response.raise_for_status()
        payload = response.json()
        assert len(payload["categories"]) == 2
        assert payload["total_open"] == 2
        assert payload["total"] == 3

    print("PASS")