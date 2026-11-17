from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, User

TIMEOUT_SECONDS = 10
router = APIRouter(prefix="/api")


def _age_days(created_at: datetime) -> int:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created_at).total_seconds()
    return max(0, int(age // 86400))


def _masked_hash_prefix(key_hash: str) -> str:
    if len(key_hash) <= 8:
        return "***"
    return f"{key_hash[:8]}***"


def get_api_key_usage_audit(db: Session, org_id: str) -> Dict[str, Any]:
    """Build a read-only API key inventory and user-role audit for an org."""
    key_rows = db.execute(
        select(ApiKey.id, ApiKey.label, ApiKey.key_hash, ApiKey.created_at)
        .where(ApiKey.org_id == org_id)
        .order_by(ApiKey.created_at, ApiKey.id)
    ).all()

    keys = []
    warnings = []
    for key_id, label, key_hash, created_at in key_rows:
        age_days = _age_days(created_at)
        keys.append(
            {
                "id": key_id,
                "label": label,
                "key_hash_prefix": _masked_hash_prefix(str(key_hash or "")),
                "created_at": created_at,
                "age_days": age_days,
            }
        )
        if age_days > 90:
            warnings.append("key_older_than_90_days")

    user_count = int(
        db.execute(
            select(func.count()).select_from(User).where(User.org_id == org_id)
        ).scalar_one()
    )

    role_rows = db.execute(
        select(User.role, func.count())
        .where(User.org_id == org_id)
        .group_by(User.role)
    ).all()
    roles = {role or "unknown": int(count) for role, count in role_rows}

    return {
        "org_id": org_id,
        "keys": keys,
        "user_count": user_count,
        "roles": roles,
        "warnings": warnings,
    }


api_key_usage_audit = get_api_key_usage_audit
audit_api_keys = get_api_key_usage_audit


@router.get("/orgs/{org_id}/api_keys/audit")
async def read_api_key_usage_audit(
    org_id: str, db: Session = Depends(get_session)
) -> Dict[str, Any]:
    return await asyncio.wait_for(
        asyncio.to_thread(get_api_key_usage_audit, db, org_id),
        timeout=TIMEOUT_SECONDS,
    )


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
    ApiKey.__table__.create(test_engine)
    User.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    seeded_hash = "abcdef0123456789fedcba9876543210"
    with TestSession() as session:
        session.add(
            ApiKey(
                id="key-1",
                org_id="org-1",
                key_hash=seeded_hash,
                label="Primary key",
                created_at=datetime.now(timezone.utc) - timedelta(days=120),
            )
        )
        session.add(
            User(
                id="user-1",
                org_id="org-1",
                email="owner@example.com",
                password_hash="not-a-real-password-hash",
                role="owner",
            )
        )
        session.add(
            User(
                id="user-2",
                org_id="org-1",
                email="member@example.com",
                password_hash="not-a-real-password-hash",
                role="member",
            )
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/org-1/api_keys/audit")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert any(str(key["id"]) == "key-1" for key in payload["keys"])
        assert seeded_hash not in response.text
        assert payload["user_count"] == 2
        assert payload["roles"] == {"owner": 1, "member": 1}
        assert "key_older_than_90_days" in payload["warnings"]

    print("PASS")