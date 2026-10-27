"""
services/staged/api_key_age_cohort_api/contract.py
"""

from __future__ import annotations

import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base, engine
from app.models import ApiKey, Org

router = APIRouter(prefix="/api", tags=["api_key_age_cohort"])


# ---------- Pydantic response models ----------
class Cohort(BaseModel):
    bucket: str
    count: int


class PerOrg(BaseModel):
    org_id: int
    count: int
    oldest_created_at: datetime.datetime
    newest_created_at: datetime.datetime


class Totals(BaseModel):
    keys: int
    orgs: int


class AgeCohortResponse(BaseModel):
    cohorts: List[Cohort]
    per_org: List[PerOrg]
    totals: Totals


# ---------- Helper functions ----------
def _bucket_name(days: int) -> str:
    if days < 7:
        return "<7d"
    if days < 30:
        return "7-30d"
    if days < 90:
        return "30-90d"
    if days < 365:
        return "90-365d"
    return ">365d"


# ---------- Endpoint ----------
@router.get(
    "/org/api-keys/age-cohorts",
    response_model=AgeCohortResponse,
    summary="Age cohorts for API keys per organisation",
)
def get_api_key_age_cohorts(db: Session = Depends(get_session)):
    now = datetime.datetime.utcnow()

    # Pull all keys with their orgs
    keys = db.query(ApiKey.id, ApiKey.org_id, ApiKey.created_at).all()
    if not keys:
        raise HTTPException(status_code=404, detail="No API keys found")

    # Compute bucket counts
    bucket_counts: dict[str, int] = {}
    per_org_data: dict[int, dict] = {}

    for key_id, org_id, created_at in keys:
        age_days = (now - created_at).days
        bucket = _bucket_name(age_days)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        org_entry = per_org_data.setdefault(
            org_id,
            {
                "count": 0,
                "oldest_created_at": created_at,
                "newest_created_at": created_at,
            },
        )
        org_entry["count"] += 1
        if created_at < org_entry["oldest_created_at"]:
            org_entry["oldest_created_at"] = created_at
        if created_at > org_entry["newest_created_at"]:
            org_entry["newest_created_at"] = created_at

    cohorts = [Cohort(bucket=b, count=c) for b, c in sorted(bucket_counts.items())]
    per_org = [
        PerOrg(
            org_id=oid,
            count=data["count"],
            oldest_created_at=data["oldest_created_at"],
            newest_created_at=data["newest_created_at"],
        )
        for oid, data in per_org_data.items()
    ]

    totals = Totals(keys=len(keys), orgs=len(per_org_data))

    return AgeCohortResponse(cohorts=cohorts, per_org=per_org, totals=totals)


# ---------- Self‑test ----------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a temporary in‑memory SQLite engine
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)

    # Create tables in the in‑memory DB
    Base.metadata.create_all(bind=test_engine)

    # Dependency override that yields sessions from the test engine
    async def get_test_session() -> Session:  # type: ignore[misc]
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    now = datetime.datetime.utcnow()
    with TestSessionLocal() as db:
        # Orgs
        org1 = Org(id=1, name="Org One", created_at=now - datetime.timedelta(days=200))
        org2 = Org(id=2, name="Org Two", created_at=now - datetime.timedelta(days=150))
        db.add_all([org1, org2])
        db.flush()  # ensure IDs are available

        # ApiKeys with varied ages
        keys = [
            ApiKey(
                id=1,
                org_id=1,
                key_hash="hash1",
                label="key1",
                created_at=now - datetime.timedelta(days=3),  # <7d
            ),
            ApiKey(
                id=2,
                org_id=1,
                key_hash="hash2",
                label="key2",
                created_at=now - datetime.timedelta(days=10),  # 7-30d
            ),
            ApiKey(
                id=3,
                org_id=2,
                key_hash="hash3",
                label="key3",
                created_at=now - datetime.timedelta(days=40),  # 30-90d
            ),
            ApiKey(
                id=4,
                org_id=2,
                key_hash="hash4",
                label="key4",
                created_at=now - datetime.timedelta(days=400),  # >365d
            ),
        ]
        db.add_all(keys)
        db.commit()

    # Build FastAPI app for the test
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    client = TestClient(test_app)

    resp = client.get("/api/org/api-keys/age-cohorts")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    # Basic structural assertions
    assert "totals" in data
    assert data["totals"]["keys"] == 4
    assert data["totals"]["orgs"] == 2
    assert len(data["cohorts"]) >= 2  # at least two non‑empty buckets

    print("PASS")
    sys.exit(0)