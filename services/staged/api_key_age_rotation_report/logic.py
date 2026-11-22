"""logic.py -- API key age and rotation reporting over the real application data layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


router = APIRouter(prefix="/api", tags=["api-key-age-rotation"])


class KeyAgeReportItem(BaseModel):
    id: str
    label: str
    created_at: str
    age_days: int
    bucket: str


class OrgKeyAgeReport(BaseModel):
    org_id: str
    org_name: Optional[str]
    key_count: int
    oldest_age_days: int
    buckets: Dict[str, int]
    keys: List[KeyAgeReportItem]


class ApiKeyAgeReportResponse(BaseModel):
    total_keys: int
    overdue_90d: int
    orgs: List[OrgKeyAgeReport]
    limit: int


_BUCKETS = ("0-30", "31-90", "91-180", "181-365", "365+")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, now: datetime) -> int:
    elapsed = now - _as_utc(created_at)
    return max(0, int(elapsed.total_seconds() // 86400))


def _bucket(age: int) -> str:
    if age <= 30:
        return "0-30"
    if age <= 90:
        return "31-90"
    if age <= 180:
        return "91-180"
    if age <= 365:
        return "181-365"
    return "365+"


def _empty_buckets() -> Dict[str, int]:
    return {name: 0 for name in _BUCKETS}


def get_api_key_age_report(
    db: Session,
    org_id: Optional[str] = None,
    limit: int = 100,
) -> Dict:
    """Return API-key age and rotation state without exposing key material."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    now = datetime.now(timezone.utc)
    query = select(
        ApiKey.id,
        ApiKey.org_id,
        ApiKey.label,
        ApiKey.created_at,
    )
    if org_id is not None:
        query = query.where(ApiKey.org_id == org_id)
    query = query.order_by(ApiKey.created_at.asc(), ApiKey.id.asc())

    rows = db.execute(query).all()

    org_ids = {str(row.org_id) for row in rows}
    org_names: Dict[str, Optional[str]] = {}
    if org_ids:
        org_rows = db.execute(
            select(Org.id, Org.name).where(Org.id.in_(org_ids))
        ).all()
        org_names = {
            str(org_id_value): name
            for org_id_value, name in org_rows
        }

    grouped: Dict[str, Dict] = {}
    total_keys = 0
    overdue_90d = 0

    for row in rows:
        key_org_id = str(row.org_id)
        age = _age_days(row.created_at, now)
        bucket = _bucket(age)
        total_keys += 1
        if age > 90:
            overdue_90d += 1

        org = grouped.setdefault(
            key_org_id,
            {
                "org_id": key_org_id,
                "org_name": org_names.get(key_org_id),
                "key_count": 0,
                "oldest_age_days": 0,
                "buckets": _empty_buckets(),
                "keys": [],
            },
        )
        org["key_count"] += 1
        org["oldest_age_days"] = max(org["oldest_age_days"], age)
        org["buckets"][bucket] += 1
        org["keys"].append(
            {
                "id": str(row.id),
                "label": row.label,
                "created_at": row.created_at.isoformat(),
                "age_days": age,
                "bucket": bucket,
            }
        )

    remaining = limit
    for org in grouped.values():
        org["keys"] = org["keys"][:remaining]
        remaining = max(0, remaining - len(org["keys"]))

    orgs = list(grouped.values())
    for org in orgs:
        org["keys"] = [
            KeyAgeReportItem(**key).model_dump()
            if hasattr(KeyAgeReportItem, "model_dump")
            else KeyAgeReportItem(**key).dict()
            for key in org["keys"]
        ]

    response = {
        "total_keys": total_keys,
        "overdue_90d": overdue_90d,
        "orgs": orgs,
        "limit": limit,
    }
    if hasattr(ApiKeyAgeReportResponse, "model_dump"):
        return ApiKeyAgeReportResponse(**response).model_dump()
    return ApiKeyAgeReportResponse(**response).dict()


# Compatibility aliases for callers that use a shorter report name.
get_report = get_api_key_age_report
api_key_age_rotation_report = get_api_key_age_report


@router.get(
    "/orgs/api-key-age-report",
    response_model=ApiKeyAgeReportResponse,
)
def api_key_age_report_endpoint(
    org_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> Dict:
    return get_api_key_age_report(db, org_id=org_id, limit=limit)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_db = TestSession()

    test_org = Org(id="org-test", name="Test Org")
    test_db.add(test_org)
    test_db.commit()

    test_now = datetime.now(timezone.utc)
    test_db.add_all(
        [
            ApiKey(
                id="key-10",
                org_id="org-test",
                label="ten-day-key",
                created_at=test_now - timedelta(days=10),
                key_hash="seed-value-1",
            ),
            ApiKey(
                id="key-60",
                org_id="org-test",
                label="sixty-day-key",
                created_at=test_now - timedelta(days=60),
                key_hash="seed-value-2",
            ),
            ApiKey(
                id="key-200",
                org_id="org-test",
                label="two-hundred-day-key",
                created_at=test_now - timedelta(days=200),
                key_hash="seed-value-3",
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_db
    client = TestClient(test_app)
    response = client.get("/api/orgs/api-key-age-report")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_keys"] == 3
    assert payload["overdue_90d"] == 1
    assert payload["orgs"][0]["buckets"]["181-365"] == 1
    assert "key_hash" not in str(payload)
    print("PASS")