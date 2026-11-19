"""logic.py -- the service's data/computation layer for API key age auditing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


DEFAULT_STALE_THRESHOLD_DAYS = 90


class APIKeyAuditItem(BaseModel):
    id: int
    label: Optional[str]
    key_hash_prefix: str
    created_at: datetime
    age_days: int
    stale: bool


class APIKeyAuditResponse(BaseModel):
    org_id: int
    org_name: Optional[str]
    total_keys: int
    stale_keys: int
    stale_threshold_days: int
    keys: List[APIKeyAuditItem]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, now: datetime) -> int:
    return max(0, int((now - _as_utc(created_at)).total_seconds() // 86400))


def audit_api_keys(
    db: Session,
    org_id: int,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    """Return the age and staleness audit for API keys belonging to an org."""
    now = datetime.now(timezone.utc)
    org = db.execute(select(Org).where(Org.id == org_id)).scalar_one_or_none()

    api_keys = db.execute(
        select(ApiKey)
        .where(ApiKey.org_id == org_id)
        .order_by(ApiKey.created_at, ApiKey.id)
    ).scalars().all()

    keys: List[Dict[str, Any]] = []
    for api_key in api_keys:
        age_days = _age_days(api_key.created_at, now)
        keys.append(
            {
                "id": api_key.id,
                "label": api_key.label,
                "key_hash_prefix": str(api_key.key_hash or "")[:8],
                "created_at": api_key.created_at,
                "age_days": age_days,
                "stale": age_days > stale_threshold_days,
            }
        )

    return {
        "org_id": org_id,
        "org_name": org.name if org is not None else None,
        "total_keys": len(keys),
        "stale_keys": sum(1 for key in keys if key["stale"]),
        "stale_threshold_days": stale_threshold_days,
        "keys": keys,
    }


def audit_org_api_keys(
    db: Session,
    org_id: int,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the organization-scoped name."""
    return audit_api_keys(db, org_id, stale_threshold_days)


def get_api_key_audit(
    db: Session,
    org_id: int,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the audit resource name."""
    return audit_api_keys(db, org_id, stale_threshold_days)


router = APIRouter(prefix="/api")


@router.get("/orgs/{org_id}/api-keys/audit", response_model=APIKeyAuditResponse)
def api_key_age_audit(
    org_id: int,
    stale_threshold_days: int = Query(
        DEFAULT_STALE_THRESHOLD_DAYS,
        ge=0,
        description="Age threshold in days; keys older than this are stale.",
    ),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Audit the age and staleness of an organization's API keys."""
    org_exists = db.execute(select(Org.id).where(Org.id == org_id)).scalar_one_or_none()
    if org_exists is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return audit_api_keys(db, org_id, stale_threshold_days)


app = FastAPI(title="Org API Key Age Audit API")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)()

    org = Org(id=1, name="Example Organization")
    now = datetime.now(timezone.utc)
    testing_session.add(org)
    testing_session.add_all(
        [
            ApiKey(
                id=1,
                org_id=1,
                key_hash="abcdefgh1234567890",
                label="old",
                created_at=now - timedelta(days=200),
            ),
            ApiKey(
                id=2,
                org_id=1,
                key_hash="12345678recent",
                label="recent-one",
                created_at=now - timedelta(days=3),
            ),
            ApiKey(
                id=3,
                org_id=1,
                key_hash="87654321recent",
                label="recent-two",
                created_at=now - timedelta(days=10),
            ),
        ]
    )
    testing_session.commit()

    app.dependency_overrides[get_session] = lambda: testing_session
    with TestClient(app) as client:
        response = client.get("/api/orgs/1/api-keys/audit")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_keys"] == 3
        assert payload["stale_keys"] == 1
        stale_key = next(key for key in payload["keys"] if key["stale"])
        assert stale_key["age_days"] > 90

    print("PASS")