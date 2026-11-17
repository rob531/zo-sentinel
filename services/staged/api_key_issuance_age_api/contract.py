import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports
from app.db import Base, get_session
from app.models import ApiKey, Org

router = APIRouter()


class Bucket(BaseModel):
    bucket: str = Field(..., description="Age bucket label")
    count: int = Field(..., description="Number of keys in bucket")


class OrgInfo(BaseModel):
    org_id: int = Field(..., description="Organization identifier")
    key_count: int = Field(..., description="Number of keys for the org")
    oldest_created_at: datetime.datetime = Field(..., description="Oldest key creation timestamp")


class IssuanceAgeResponse(BaseModel):
    days: int = Field(..., description="Query window in days")
    total_keys: int = Field(..., description="Total number of active keys")
    buckets: List[Bucket] = Field(..., description="Key counts per age bucket")
    orgs: List[OrgInfo] = Field(..., description="Top 20 orgs by key count")


def _bucket_label(age_days: int) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return "91d+"


@router.get(
    "/api/orgs/api-keys/issuance-age",
    response_model=IssuanceAgeResponse,
    summary="API key issuance age distribution",
)
def get_issuance_age(
    days: int = Query(..., ge=1, description="Number of days to look back"),
    db: Session = Depends(get_session),
):
    now = datetime.datetime.utcnow()

    # Fetch all keys (filtering by days is optional; we include all for bucket totals)
    keys = db.execute(select(ApiKey)).scalars().all()

    # Bucket counts
    bucket_counts = {"0-7d": 0, "8-30d": 0, "31-90d": 0, "91d+": 0}
    for key in keys:
        age = (now - key.created_at).days
        label = _bucket_label(age)
        bucket_counts[label] += 1

    buckets = [Bucket(bucket=k, count=v) for k, v in bucket_counts.items()]

    total_keys = len(keys)

    # Org aggregation
    org_stats = {}
    for key in keys:
        org_id = key.org_id
        if org_id not in org_stats:
            org_stats[org_id] = {"count": 0, "oldest": key.created_at}
        org_stats[org_id]["count"] += 1
        if key.created_at < org_stats[org_id]["oldest"]:
            org_stats[org_id]["oldest"] = key.created_at

    # Build OrgInfo list, sorted by count desc, limited to top 20
    orgs = [
        OrgInfo(
            org_id=oid,
            key_count=data["count"],
            oldest_created_at=data["oldest"],
        )
        for oid, data in org_stats.items()
    ]
    orgs.sort(key=lambda o: o.key_count, reverse=True)
    orgs = orgs[:20]

    return IssuanceAgeResponse(
        days=days,
        total_keys=total_keys,
        buckets=buckets,
        orgs=orgs,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a local FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Override the session dependency
    def get_test_session() -> Session:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with TestingSessionLocal() as db:
        now = datetime.datetime.utcnow()
        # Orgs
        org1 = Org(id=1, name="Org One", created_at=now - timedelta(days=200))
        org2 = Org(id=2, name="Org Two", created_at=now - timedelta(days=150))
        org3 = Org(id=3, name="Org Three", created_at=now - timedelta(days=100))
        db.add_all([org1, org2, org3])
        db.flush()

        # ApiKeys with varying ages
        keys = [
            # Org 1
            ApiKey(
                id=1,
                org_id=1,
                key_hash="hash1",
                label="key1",
                created_at=now - timedelta(days=5),
            ),
            ApiKey(
                id=2,
                org_id=1,
                key_hash="hash2",
                label="key2",
                created_at=now - timedelta(days=20),
            ),
            ApiKey(
                id=3,
                org_id=1,
                key_hash="hash3",
                label="key3",
                created_at=now - timedelta(days=100),
            ),
            # Org 2
            ApiKey(
                id=4,
                org_id=2,
                key_hash="hash4",
                label="key4",
                created_at=now - timedelta(days=10),
            ),
            ApiKey(
                id=5,
                org_id=2,
                key_hash="hash5",
                label="key5",
                created_at=now - timedelta(days=40),
            ),
            # Org 3
            ApiKey(
                id=6,
                org_id=3,
                key_hash="hash6",
                label="key6",
                created_at=now - timedelta(days=200),
            ),
        ]
        db.add_all(keys)
        db.commit()

    client = TestClient(app)

    resp = client.get("/api/orgs/api-keys/issuance-age?days=365")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    total_keys = data["total_keys"]
    bucket_sum = sum(b["count"] for b in data["buckets"])

    if total_keys != bucket_sum:
        print("FAIL: bucket sum does not match total_keys", file=sys.stderr)
        sys.exit(1)

    # Ensure at least one key falls into the 91d+ bucket
    bucket_91 = next((b for b in data["buckets"] if b["bucket"] == "91d+"), None)
    if not bucket_91 or bucket_91["count"] == 0:
        print("FAIL: no keys in 91d+ bucket", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)