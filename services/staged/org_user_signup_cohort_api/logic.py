from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import Org, User


router = APIRouter(prefix="/api")


class RoleCount(BaseModel):
    role: str
    count: int


class SignupCohort(BaseModel):
    month: str
    user_count: int
    distinct_orgs: int
    roles: List[RoleCount]


class OrgSignupTotal(BaseModel):
    org_id: str
    org_name: str
    user_count: int


class SignupCohortResponse(BaseModel):
    months: int
    total_users: int
    total_orgs: int
    cohorts: List[SignupCohort]
    orgs: Optional[List[OrgSignupTotal]] = None
    generated_at: str


def _utc_month(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("created_at must be a datetime or ISO-8601 string")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m")


def _month_cutoff(months: int) -> datetime:
    now = datetime.now(timezone.utc)
    total_months = now.year * 12 + now.month - 1 - (months - 1)
    year, zero_based_month = divmod(total_months, 12)
    return datetime(year, zero_based_month + 1, 1)


@router.get(
    "/orgs/cohorts",
    response_model=SignupCohortResponse,
    response_model_exclude_none=True,
)
def get_org_user_signup_cohorts(
    org_id: Optional[str] = Query(default=None),
    months: int = Query(default=12, ge=1, le=36),
    session=Depends(get_session),
) -> SignupCohortResponse:
    cutoff = _month_cutoff(months)

    users_query = session.query(
        User.id,
        User.email,
        User.org_id,
        User.role,
        User.created_at,
    ).filter(User.created_at >= cutoff)

    orgs_query = session.query(Org.id, Org.name, Org.created_at)

    if org_id is not None:
        users_query = users_query.filter(User.org_id == org_id)
        orgs_query = orgs_query.filter(Org.id == org_id)

    user_rows = users_query.all()
    org_rows = orgs_query.all()

    if org_id is not None and not org_rows:
        raise HTTPException(status_code=404, detail="Organisation not found")

    month_users: Counter[str] = Counter()
    month_orgs: Dict[str, set[str]] = defaultdict(set)
    month_roles: Dict[str, Counter[str]] = defaultdict(Counter)
    org_user_counts: Counter[str] = Counter()

    for user_id, email, user_org_id, role, created_at in user_rows:
        month = _utc_month(created_at)
        normalized_org_id = str(user_org_id)
        normalized_role = str(role) if role is not None else "unknown"

        month_users[month] += 1
        month_orgs[month].add(normalized_org_id)
        month_roles[month][normalized_role] += 1
        org_user_counts[normalized_org_id] += 1

    cohorts: List[SignupCohort] = []
    for month in sorted(month_users):
        cohorts.append(
            SignupCohort(
                month=month,
                user_count=month_users[month],
                distinct_orgs=len(month_orgs[month]),
                roles=[
                    RoleCount(role=role, count=count)
                    for role, count in sorted(month_roles[month].items())
                ],
            )
        )

    org_totals: Optional[List[OrgSignupTotal]] = None
    if org_id is not None:
        actual_org_id, org_name, _ = org_rows[0]
        normalized_org_id = str(actual_org_id)
        org_totals = [
            OrgSignupTotal(
                org_id=normalized_org_id,
                org_name=str(org_name),
                user_count=org_user_counts[normalized_org_id],
            )
        ]

    represented_orgs = {
        str(user_org_id)
        for _, _, user_org_id, _, _ in user_rows
        if user_org_id is not None
    }

    return SignupCohortResponse(
        months=months,
        total_users=len(user_rows),
        total_orgs=len(represented_orgs),
        cohorts=cohorts,
        orgs=org_totals,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id VARCHAR PRIMARY KEY,
                    email VARCHAR NOT NULL,
                    org_id VARCHAR,
                    role VARCHAR,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        current_month = now.replace(day=15, hour=12, minute=0, second=0, microsecond=0)
        previous_month_date = (current_month.replace(day=1) - __import__("datetime").timedelta(days=1)).replace(
            day=15,
            hour=12,
            minute=0,
            second=0,
            microsecond=0,
        )

        connection.execute(
            text("INSERT INTO orgs (id, name, created_at) VALUES (:id, :name, :created_at)"),
            [
                {"id": "org-a", "name": "Alpha", "created_at": previous_month_date},
                {"id": "org-b", "name": "Beta", "created_at": previous_month_date},
                {"id": "org-c", "name": "Gamma", "created_at": previous_month_date},
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, org_id, role, created_at)
                VALUES (:id, :email, :org_id, :role, :created_at)
                """
            ),
            [
                {
                    "id": "user-1",
                    "email": "one@example.com",
                    "org_id": "org-a",
                    "role": "admin",
                    "created_at": current_month,
                },
                {
                    "id": "user-2",
                    "email": "two@example.com",
                    "org_id": "org-a",
                    "role": "member",
                    "created_at": current_month,
                },
                {
                    "id": "user-3",
                    "email": "three@example.com",
                    "org_id": "org-b",
                    "role": "member",
                    "created_at": current_month,
                },
                {
                    "id": "user-4",
                    "email": "four@example.com",
                    "org_id": "org-b",
                    "role": "admin",
                    "created_at": previous_month_date,
                },
                {
                    "id": "user-5",
                    "email": "five@example.com",
                    "org_id": "org-c",
                    "role": "member",
                    "created_at": previous_month_date,
                },
            ],
        )

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/cohorts", params={"months": 12})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_users"] == 5
        assert len(payload["cohorts"]) == 2
        largest = max(payload["cohorts"], key=lambda cohort: cohort["user_count"])
        assert sum(role["count"] for role in largest["roles"]) == largest["user_count"]

        filtered = client.get(
            "/api/orgs/cohorts",
            params={"months": 12, "org_id": "org-a"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert len(filtered_payload["orgs"]) == 1
        assert filtered_payload["orgs"][0]["org_id"] == "org-a"

    print("PASS")