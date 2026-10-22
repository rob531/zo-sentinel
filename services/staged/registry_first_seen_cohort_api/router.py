from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_first_seen_cohort

router = APIRouter()


class FirstSeenCohort(BaseModel):
    date: str
    total: int
    by_tier: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)


class FirstSeenCohortResponse(BaseModel):
    days: int
    cohorts: list[FirstSeenCohort] = Field(default_factory=list)


@router.get(
    "/registry/first-seen-cohort",
    response_model=FirstSeenCohortResponse,
)
def first_seen_cohort_endpoint(
    days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> dict:
    return get_first_seen_cohort(session=session, days=days)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    test_session = TestSession()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="cohort-a",
                first_seen=now - timedelta(days=1, hours=1),
                last_seen=now,
                risk_tier="high",
                registry_source="scan",
            ),
            McpServerRegistry(
                server_id="cohort-b",
                first_seen=now - timedelta(days=1, hours=2),
                last_seen=now,
                risk_tier="low",
                registry_source="scan",
            ),
            McpServerRegistry(
                server_id="cohort-c",
                first_seen=now - timedelta(days=2, hours=1),
                last_seen=now,
                risk_tier="high",
                registry_source="manual",
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = lambda: test_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/first-seen-cohort",
            params={"days": 7},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["cohorts"]) == 2, payload
        assert sorted(cohort["total"] for cohort in payload["cohorts"]) == [1, 2], payload

    print("PASS")