"""logic.py -- the service's data/computation layer (one concern, one file).

Reads the real application models through the application SQLAlchemy session.
The router is responsible for HTTP response models and status codes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return _as_utc(value).isoformat()


def get_org_api_key_audit(
    db: Session,
    org_id: str,
    max_age_days: int = 90,
) -> Optional[Dict[str, Any]]:
    """Return the API-key rotation audit for an organisation.

    ``None`` means that the organisation does not exist.  Key material is never
    returned: only an eight-character prefix and an ellipsis are exposed.
    """
    if max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")

    org = db.execute(select(Org).where(Org.id == org_id)).scalar_one_or_none()
    if org is None:
        return None

    api_keys = list(
        db.execute(
            select(ApiKey)
            .where(ApiKey.org_id == org_id)
            .order_by(ApiKey.created_at.asc(), ApiKey.id.asc())
        )
        .scalars()
        .all()
    )

    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=max_age_days)
    keys: list[Dict[str, Any]] = []
    ages: list[int] = []

    for api_key in api_keys:
        created_at = _as_utc(api_key.created_at)
        age = max(0, (now - created_at).days)
        ages.append(age)
        keys.append(
            {
                "id": str(api_key.id),
                "label": api_key.label,
                "hash_prefix": f"{(api_key.key_hash or '')[:8]}...",
                "created_at": _isoformat(api_key.created_at),
                "age_days": age,
                "status": "rotate" if created_at < threshold else "ok",
            }
        )

    return {
        "org_id": str(org.id),
        "org_name": org.name,
        "max_age_days": max_age_days,
        "total_keys": len(keys),
        "rotation_due": sum(1 for key in keys if key["status"] == "rotate"),
        "oldest_age_days": max(ages) if ages else None,
        "newest_age_days": min(ages) if ages else None,
        "keys": keys,
    }


def audit_api_keys(
    db: Session,
    org_id: str,
    max_age_days: int = 90,
) -> Optional[Dict[str, Any]]:
    """Compatibility alias for the organisation API-key audit computation."""
    return get_org_api_key_audit(db, org_id, max_age_days)


def get_api_key_audit(
    db: Session,
    org_id: str,
    max_age_days: int = 90,
) -> Optional[Dict[str, Any]]:
    """Compatibility alias for :func:`get_org_api_key_audit`."""
    return get_org_api_key_audit(db, org_id, max_age_days)


def _self_test() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    db = SessionLocal()

    now = datetime.now(timezone.utc)
    db.add(
        Org(
            id="audit-org",
            name="Audit Organisation",
            created_at=now,
        )
    )
    db.add_all(
        [
            ApiKey(
                id="audit-key-old-1",
                org_id="audit-org",
                key_hash="old-key-material-one",
                label="old one",
                created_at=now - timedelta(days=120),
            ),
            ApiKey(
                id="audit-key-old-2",
                org_id="audit-org",
                key_hash="old-key-material-two",
                label="old two",
                created_at=now - timedelta(days=100),
            ),
            ApiKey(
                id="audit-key-new",
                org_id="audit-org",
                key_hash="new-key-material",
                label="new",
                created_at=now - timedelta(days=5),
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/orgs/{org_id}/api-key-audit")
    def api_key_audit(org_id: str, max_age_days: int = 90, db=Depends(get_session)):
        result = get_org_api_key_audit(db, org_id, max_age_days)
        if result is None:
            raise HTTPException(status_code=404, detail="Organization not found")
        return result

    client = TestClient(test_app)
    response = client.get("/api/orgs/audit-org/api-key-audit")
    assert response.status_code == 200
    body = response.json()
    assert body["total_keys"] == 3
    assert body["rotation_due"] == 2
    assert all(len(key["hash_prefix"]) <= 12 for key in body["keys"])
    assert all("key_hash" not in key for key in body["keys"])
    assert client.get("/api/orgs/missing-org/api-key-audit").status_code == 404
    print("PASS")


if __name__ == "__main__":
    _self_test()