# services/staged/org_api_key_age_rollup_api/logic.py
"""logic.py -- API-key rotation-hygiene rollup for org admins.

Pure computation over the REAL app data layer (app.db / app.models). Reads
only metadata from api_keys; key_hash is never emitted. No writes, no raw SQL,
no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session  # noqa: F401  (re-exported for router Depends)
from app.models import ApiKey


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    dt = _as_utc(value)
    return dt.isoformat() if dt is not None else None


def bucket_for_age_days(age_days: float) -> str:
    """Map an age in days to lt_30d / d30_90 / gt_90d."""
    if age_days < 30:
        return "lt_30d"
    if age_days <= 90:
        return "d30_90"
    return "gt_90d"


def empty_buckets() -> Dict[str, int]:
    return {"lt_30d": 0, "d30_90": 0, "gt_90d": 0}


def get_api_key_age_rollup(db: Session, org_id: str) -> Dict[str, Any]:
    """Per-org API-key rotation hygiene summary (metadata only)."""
    now = _now_utc()
    rows = db.execute(
        select(ApiKey)
        .where(ApiKey.org_id == org_id)
        .order_by(ApiKey.created_at.asc(), ApiKey.id.asc())
    ).scalars().all()

    buckets = empty_buckets()
    keys: List[Dict[str, Any]] = []
    oldest: Optional[datetime] = None
    newest: Optional[datetime] = None

    for key in rows:
        created = _as_utc(getattr(key, "created_at", None))
        if created is None:
            continue
        age_days = int((now - created).total_seconds() // 86400)
        if age_days < 0:
            age_days = 0
        bucket = bucket_for_age_days(age_days)
        buckets[bucket] += 1
        if oldest is None or created < oldest:
            oldest = created
        if newest is None or created > newest:
            newest = created
        keys.append(
            {
                "id": getattr(key, "id", None),
                "label": getattr(key, "label", None),
                "created_at": _iso(created),
                "age_days": age_days,
                "bucket": bucket,
            }
        )

    return {
        "org_id": org_id,
        "key_count": len(keys),
        "oldest_created_at": _iso(oldest),
        "newest_created_at": _iso(newest),
        "age_buckets": buckets,
        "keys": keys,
    }


# Convenience alias used by the router.
key_age_rollup = get_api_key_age_rollup


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    now = datetime.now(timezone.utc)
    org_a = "org-alpha"
    org_b = "org-beta"
    secret_a = "hash-alpha-secret"
    secret_b = "hash-beta-secret"

    session.add(ApiKey(id="k1", org_id=org_a, key_hash=secret_a, label="ci", created_at=now - timedelta(days=2)))
    session.add(ApiKey(id="k2", org_id=org_a, key_hash=secret_a + "-2", label="staging", created_at=now - timedelta(days=10)))
    session.add(ApiKey(id="k3", org_id=org_a, key_hash=secret_a + "-3", label="legacy", created_at=now - timedelta(days=200)))
    session.add(ApiKey(id="k4", org_id=org_b, key_hash=secret_b, label="other-org", created_at=now - timedelta(days=3)))
    session.commit()

    that_app = FastAPI()

    @that_app.get("/api/orgs/{org_id}/api-keys/age")
    def _route(org_id: str, db: Session = __import__("fastapi").Depends(get_session)):
        return get_api_key_age_rollup(db, org_id)

    that_app.dependency_overrides[get_session] = lambda: session

    client = TestClient(that_app)
    resp = client.get(f"/api/orgs/{org_a}/api-keys/age")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["org_id"] == org_a
    assert data["key_count"] == 3, data
    assert sum(data["age_buckets"].values()) == 3, data
    assert set(data["age_buckets"].keys()) == {"lt_30d", "d30_90", "gt_90d"}
    assert data["age_buckets"]["lt_30d"] == 2, data
    assert data["age_buckets"]["gt_90d"] == 1, data
    assert data["oldest_created_at"] is not None
    assert data["newest_created_at"] is not None

    ids = [k["id"] for k in data["keys"]]
    assert "k4" not in ids, data
    blob = resp.text
    assert secret_b not in blob, "foreign key hash leaked"
    assert secret_a not in blob, "key hash leaked"
    assert "key_hash" not in blob, "key_hash field leaked"

    print("PASS")


if __name__ == "__main__":
    _self_test()