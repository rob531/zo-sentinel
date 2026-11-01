"""logic.py -- API key inventory and recency logic.

Provides a read-only FastAPI endpoint over the real application database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


router = APIRouter(prefix="/api", tags=["api-key-usage"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def list_api_keys(
    db: Session,
    org_id: Optional[str] = None,
    include_unused_days: int = 90,
) -> Dict[str, Any]:
    """Return API key inventory with organization names and age information."""
    statement = select(ApiKey, Org.name).join(Org, Org.id == ApiKey.org_id)
    if org_id is not None:
        statement = statement.where(ApiKey.org_id == org_id)

    rows = db.execute(statement).all()
    now = datetime.now(timezone.utc)
    keys = []
    stale_count = 0

    for api_key, org_name in rows:
        created_at = _as_utc(api_key.created_at)
        age = now - created_at
        age_days = max(0, int(age.total_seconds() // 86400))
        stale = age > timedelta(days=include_unused_days)
        if stale:
            stale_count += 1

        keys.append(
            {
                "id": api_key.id,
                "org_id": api_key.org_id,
                "org_name": org_name,
                "label": api_key.label,
                "key_hash_preview": f"{str(api_key.key_hash)[:8]}...",
                "created_at": api_key.created_at,
                "age_days": age_days,
                "last_used_hint": None,
                "stale": stale,
            }
        )

    return {
        "keys": keys,
        "count": len(keys),
        "stale_count": stale_count,
    }


@router.get("/org/api-keys")
def get_api_keys(
    org_id: Optional[str] = Query(default=None),
    include_unused_days: int = Query(default=90, ge=0),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return list_api_keys(
        db=db,
        org_id=org_id,
        include_unused_days=include_unused_days,
    )


if __name__ == "__main__":
    from datetime import timedelta
    from uuid import uuid4

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
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    Org.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)

    session = TestingSessionLocal()
    org_a = Org(id=str(uuid4()), name="Organization A")
    org_b = Org(id=str(uuid4()), name="Organization B")
    session.add_all([org_a, org_b])
    session.flush()

    session.add_all(
        [
            ApiKey(
                id=str(uuid4()),
                org_id=org_a.id,
                key_hash="hash-alpha-0123456789",
                label="alpha",
            ),
            ApiKey(
                id=str(uuid4()),
                org_id=org_b.id,
                key_hash="hash-bravo-0123456789",
                label="bravo",
            ),
            ApiKey(
                id=str(uuid4()),
                org_id=org_a.id,
                key_hash="hash-charlie-0123456789",
                label="charlie",
                created_at=datetime.now(timezone.utc) - timedelta(days=120),
            ),
        ]
    )
    session.commit()
    session.close()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        db_session = TestingSessionLocal()
        try:
            yield db_session
        finally:
            db_session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/org/api-keys")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 3
        assert len(payload["keys"]) == 3
        assert payload["stale_count"] == 1
        assert {key["org_name"] for key in payload["keys"]} == {
            "Organization A",
            "Organization B",
        }
        for key in payload["keys"]:
            assert "key_hash" not in key
            assert key["key_hash_preview"].endswith("...")
            assert key["key_hash_preview"] != key["label"]
            assert key["last_used_hint"] is None

    print("PASS")