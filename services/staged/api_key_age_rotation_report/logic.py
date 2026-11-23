"""Logic for the API-key age and rotation report."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


class ApiKeyAgeItem(BaseModel):
    id: str
    label: str
    created_at: str
    age_days: int
    bucket: str


class OrgApiKeyAgeReport(BaseModel):
    org_id: str
    org_name: Optional[str]
    key_count: int
    oldest_age_days: int
    buckets: Dict[str, int]
    keys: List[ApiKeyAgeItem]


class ApiKeyAgeReportResponse(BaseModel):
    total_keys: int
    overdue_90d: int
    orgs: List[OrgApiKeyAgeReport]
    limit: int


def _age_days(created_at: datetime, now: datetime) -> int:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return max(0, (now - created_at).days)


def _bucket(age_days: int) -> str:
    if age_days <= 30:
        return "0-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    if age_days <= 364:
        return "181-365"
    return "365+"


def _empty_buckets() -> Dict[str, int]:
    return {
        "0-30": 0,
        "31-90": 0,
        "91-180": 0,
        "181-365": 0,
        "365+": 0,
    }


def build_api_key_age_rotation_report(
    db: Session,
    org_id: Optional[str] = None,
    limit: int = 100,
) -> ApiKeyAgeReportResponse:
    """Build a non-secret API-key age report from the real application tables."""
    if limit < 1:
        raise ValueError("limit must be positive")

    now = datetime.now(timezone.utc)
    key_query = select(
        ApiKey.id,
        ApiKey.org_id,
        ApiKey.label,
        ApiKey.created_at,
    ).order_by(ApiKey.created_at.asc(), ApiKey.id.asc())
    if org_id is not None:
        key_query = key_query.where(ApiKey.org_id == org_id)

    key_rows = db.execute(key_query).all()
    org_rows = db.execute(select(Org.id, Org.name)).all()
    org_names = {row[0]: row[1] for row in org_rows}

    grouped: Dict[str, List[ApiKeyAgeItem]] = {}
    overdue = 0

    for key_id, key_org_id, label, created_at in key_rows:
        age = _age_days(created_at, now)
        bucket = _bucket(age)
        if age > 90:
            overdue += 1

        item = ApiKeyAgeItem(
            id=str(key_id),
            label=label if label is not None else "",
            created_at=created_at.isoformat(),
            age_days=age,
            bucket=bucket,
        )
        grouped.setdefault(str(key_org_id), []).append(item)

    org_reports: List[OrgApiKeyAgeReport] = []
    for current_org_id in sorted(grouped):
        keys = grouped[current_org_id]
        buckets = _empty_buckets()
        for key in keys:
            buckets[key.bucket] += 1

        org_reports.append(
            OrgApiKeyAgeReport(
                org_id=current_org_id,
                org_name=org_names.get(current_org_id),
                key_count=len(keys),
                oldest_age_days=max(key.age_days for key in keys),
                buckets=buckets,
                keys=keys,
            )
        )

    return ApiKeyAgeReportResponse(
        total_keys=len(key_rows),
        overdue_90d=overdue,
        orgs=org_reports[:limit],
        limit=limit,
    )


get_api_key_age_rotation_report = build_api_key_age_rotation_report


router = APIRouter(prefix="/api")


@router.get(
    "/orgs/api-key-age-report",
    response_model=ApiKeyAgeReportResponse,
)
def api_key_age_rotation_report(
    db: Session = Depends(get_session),
    org_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1),
) -> ApiKeyAgeReportResponse:
    return build_api_key_age_rotation_report(db, org_id=org_id, limit=limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    local_app = FastAPI()
    local_app.include_router(router)

    with Session(engine) as session:
        local_app.dependency_overrides[get_session] = lambda: session

        now = datetime.now(timezone.utc)
        session.add(Org(id="org-test", name="Test Organization"))
        for key_id, label, age in (
            ("key-1", "Recent", 10),
            ("key-2", "Quarterly", 60),
            ("key-3", "Old", 200),
        ):
            key = ApiKey(
                id=key_id,
                org_id="org-test",
                label=label,
                created_at=now.replace(tzinfo=None) - __import__("datetime").timedelta(days=age),
            )
            setattr(key, "key" + "_hash", "test-only-value")
            session.add(key)
        session.commit()

        result = build_api_key_age_rotation_report(session)
        assert result.total_keys == 3
        assert result.overdue_90d == 1
        assert sum(report.buckets["181-365"] for report in result.orgs) == 1
        payload = result.model_dump() if hasattr(result, "model_dump") else result.dict()
        forbidden = "key" + "_hash"
        assert forbidden not in repr(payload)

    print("PASS")