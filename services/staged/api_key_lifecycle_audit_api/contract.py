from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.db import get_session
from app.models import ApiKey, Org

router = APIRouter(prefix="/api")


class LifecycleKey(BaseModel):
    key_id: str
    org_id: str
    label: Optional[str]
    created_at: datetime
    age_days: int
    status: str
    key_hash: str


class LifecycleSummary(BaseModel):
    total: int
    by_age_bucket: Dict[str, int]
    never_used: int


class LifecycleResponse(BaseModel):
    as_of: datetime
    keys: List[LifecycleKey]
    summary: LifecycleSummary


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _display_hash(value: object) -> str:
    stored = str(value or "")
    if len(stored) == 64:
        try:
            bytes.fromhex(stored)
        except ValueError:
            pass
        else:
            return stored[:8]
    return hashlib.sha256(stored.encode("utf-8")).hexdigest()[:8]


def _bucket_label(age_days: int, bucket_days: int) -> str:
    bucket_index = age_days // bucket_days
    first_day = bucket_index * bucket_days + 1
    last_day = first_day + bucket_days - 1
    return f"{first_day}-{last_day}"


@router.get("/keys/lifecycle", response_model=LifecycleResponse)
def get_api_key_lifecycle(
    org_id: Optional[str] = Query(default=None),
    age_bucket_days: int = Query(default=30, ge=1),
    db=Depends(get_session),
) -> LifecycleResponse:
    as_of = _utc_now()
    org_rows = db.execute(select(Org)).scalars().all()
    org_ids = {str(row.id) for row in org_rows}

    key_rows = db.execute(
        select(ApiKey).order_by(ApiKey.created_at.asc(), ApiKey.id.asc())
    ).scalars().all()

    keys: List[LifecycleKey] = []
    by_age_bucket: Dict[str, int] = {}

    for row in key_rows:
        row_org_id = str(row.org_id)
        if row_org_id not in org_ids:
            continue
        if org_id is not None and row_org_id != org_id:
            continue

        created_at = _as_utc(row.created_at)
        age_days = max(0, int((as_of - created_at).total_seconds() // 86400))
        bucket = _bucket_label(age_days, age_bucket_days)
        by_age_bucket[bucket] = by_age_bucket.get(bucket, 0) + 1

        keys.append(
            LifecycleKey(
                key_id=str(row.id),
                org_id=row_org_id,
                label=row.label,
                created_at=created_at,
                age_days=age_days,
                status="active" if age_days < age_bucket_days else "aging",
                key_hash=_display_hash(row.key_hash),
            )
        )

    return LifecycleResponse(
        as_of=as_of,
        keys=keys,
        summary=LifecycleSummary(
            total=len(keys),
            by_age_bucket=by_age_bucket,
            never_used=len(keys),
        ),
    )


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    full_hashes = [
        "a" * 64,
        "b" * 64,
        "c" * 64,
    ]
    now = datetime.now(timezone.utc)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                Org(id="org-1", name="Organization One", created_at=now),
                Org(id="org-2", name="Organization Two", created_at=now),
            ]
        )
        session.add_all(
            [
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    key_hash=full_hashes[0],
                    label="Primary",
                    created_at=now,
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-1",
                    key_hash=full_hashes[1],
                    label="Secondary",
                    created_at=now,
                ),
                ApiKey(
                    id="key-3",
                    org_id="org-2",
                    key_hash=full_hashes[2],
                    label="Reporting",
                    created_at=now,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/keys/lifecycle")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["keys"]) == 3
    assert payload["summary"]["total"] == 3

    for key in payload["keys"]:
        assert len(key["key_hash"]) == 8
        assert key["key_hash"] != ""

    response_text = response.text
    for full_hash in full_hashes:
        assert full_hash not in response_text

    print("PASS")


if __name__ == "__main__":
    _self_test()