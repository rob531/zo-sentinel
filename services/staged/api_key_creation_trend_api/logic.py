from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

router = APIRouter(prefix="/api", tags=["api-key-creation-trend"])


class CreationTrendPoint(BaseModel):
    date: date_type
    count: int


class OrganizationKeyCount(BaseModel):
    org_id: int
    org_name: str
    key_count: int


class ApiKeyCreationTrendResponse(BaseModel):
    days: int
    series: List[CreationTrendPoint]
    orgs: List[OrganizationKeyCount]
    total_keys: int


def _utc_date(value: Union[datetime, date_type, str]) -> date_type:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date_type):
        return value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date()


@router.get(
    "/api-keys/creation-trend",
    response_model=ApiKeyCreationTrendResponse,
)
def get_api_key_creation_trend(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ApiKeyCreationTrendResponse:
    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=days - 1)

    key_rows = (
        db.query(ApiKey.org_id, ApiKey.created_at)
        .filter(ApiKey.created_at >= datetime.combine(first_day, time.min))
        .all()
    )
    org_rows = db.query(Org.id, Org.name).order_by(Org.id).all()

    counts_by_date: Dict[date_type, int] = defaultdict(int)
    counts_by_org: Dict[int, int] = defaultdict(int)

    for org_id, created_at in key_rows:
        created_day = _utc_date(created_at)
        if created_day < first_day or created_day > today:
            continue
        counts_by_date[created_day] += 1
        counts_by_org[int(org_id)] += 1

    series = [
        CreationTrendPoint(
            date=first_day + timedelta(days=offset),
            count=counts_by_date[first_day + timedelta(days=offset)],
        )
        for offset in range(days)
    ]
    orgs = [
        OrganizationKeyCount(
            org_id=int(org_id),
            org_name=org_name,
            key_count=counts_by_org[int(org_id)],
        )
        for org_id, org_name in org_rows
    ]

    return ApiKeyCreationTrendResponse(
        days=days,
        series=series,
        orgs=orgs,
        total_keys=sum(counts_by_org.values()),
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
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE orgs (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL)")
        )
        connection.execute(
            text(
                "CREATE TABLE api_keys ("
                "id INTEGER PRIMARY KEY, "
                "org_id INTEGER NOT NULL, "
                "label VARCHAR, "
                "created_at DATETIME NOT NULL"
                ")"
            )
        )
        connection.execute(
            text("INSERT INTO orgs (id, name) VALUES (1, 'Example Org')")
        )
        connection.execute(
            text("INSERT INTO orgs (id, name) VALUES (2, 'Second Org')")
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                "INSERT INTO api_keys (id, org_id, label, created_at) "
                "VALUES (:id, :org_id, :label, :created_at)"
            ),
            [
                {
                    "id": 1,
                    "org_id": 1,
                    "label": "key-1",
                    "created_at": now - timedelta(days=2),
                },
                {
                    "id": 2,
                    "org_id": 1,
                    "label": "key-2",
                    "created_at": now - timedelta(days=1),
                },
                {
                    "id": 3,
                    "org_id": 2,
                    "label": "key-3",
                    "created_at": now,
                },
            ],
        )

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/api-keys/creation-trend?days=30")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_keys"] == 3
        assert len(payload["series"]) >= 1
        assert sum(point["count"] for point in payload["series"]) == 3

    print("PASS")