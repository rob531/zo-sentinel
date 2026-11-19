"""HTTP surface for user account age cohort reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_age_cohort_data

router = APIRouter(prefix="/api/users", tags=["users"])


class AgeCohortEntry(BaseModel):
    bucket: str
    count: int


class RoleCohortEntry(BaseModel):
    role: str
    count: int


class OrgCohortEntry(BaseModel):
    org_id: str
    count: int


class AgeCohortResponse(BaseModel):
    days: int
    cohorts: list[AgeCohortEntry]
    total: int
    by_role: list[RoleCohortEntry]
    by_org: list[OrgCohortEntry]


@router.get("/age-cohort", response_model=AgeCohortResponse)
def get_age_cohort(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> AgeCohortResponse:
    result = get_age_cohort_data(db, days=days)
    if isinstance(result, BaseModel):
        payload = (
            result.model_dump()
            if hasattr(result, "model_dump")
            else result.dict()
        )
    else:
        payload = dict(result)
    payload.setdefault("days", days)
    return AgeCohortResponse(**payload)


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import User

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False)
    now = datetime.utcnow()
    rows = [
        ("user-1", "one@example.com", "org-1", "admin", 1),
        ("user-2", "two@example.com", "org-1", "member", 5),
        ("user-3", "three@example.com", "org-2", "viewer", 10),
        ("user-4", "four@example.com", "org-2", "member", 20),
        ("user-5", "five@example.com", "org-1", "admin", 40),
        ("user-6", "six@example.com", "org-3", "viewer", 100),
        ("user-7", "seven@example.com", "org-3", "member", 300),
        ("user-8", "eight@example.com", "org-1", "admin", 500),
    ]
    with TestingSessionLocal() as session:
        for user_id, email, org_id, role, age in rows:
            session.add(
                User(
                    id=user_id,
                    email=email,
                    org_id=org_id,
                    password_hash="not-a-real-password",
                    role=role,
                    created_at=now - timedelta(days=age),
                )
            )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = (
        lambda: TestingSessionLocal()
    )

    response = TestClient(test_app).get(
        "/api/users/age-cohort", params={"days": 1000}
    )
    response.raise_for_status()
    body = response.json()
    assert body["total"] == len(rows)
    assert sum(item["count"] for item in body["cohorts"]) == len(rows)
    assert {item["role"] for item in body["by_role"]} == {
        "admin",
        "member",
        "viewer",
    }
    assert {item["org_id"] for item in body["by_org"]} == {
        "org-1",
        "org-2",
        "org-3",
    }
    print("PASS")