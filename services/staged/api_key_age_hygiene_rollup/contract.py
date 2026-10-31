from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


router = APIRouter(prefix="/api")


class KeyHygieneItem(BaseModel):
    id: str
    org_id: str
    org_name: Optional[str]
    label: str
    age_days: int
    created_at: datetime
    stale: bool


class KeyHygieneResponse(BaseModel):
    max_age_days: int
    total_keys: int
    stale_keys: int
    keys: list[KeyHygieneItem]


def clamp_max_age_days(max_age_days: int) -> int:
    return max(1, min(3650, max_age_days))


def calculate_age_days(created_at: datetime, now: Optional[datetime] = None) -> int:
    current = now or datetime.now(timezone.utc)
    created = created_at

    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    else:
        created = created.astimezone(timezone.utc)

    return max(0, (current - created).days)


def get_key_hygiene_rollup(
    session: Session,
    max_age_days: int = 90,
    now: Optional[datetime] = None,
) -> dict:
    threshold = clamp_max_age_days(max_age_days)
    current = now or datetime.now(timezone.utc)

    statement = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.label,
            ApiKey.created_at,
            Org.name,
        )
        .select_from(ApiKey)
        .outerjoin(Org, ApiKey.org_id == Org.id)
        .order_by(ApiKey.id)
    )

    rows = session.execute(statement).all()
    keys = []

    for row in rows:
        age_days = calculate_age_days(row.created_at, current)
        keys.append(
            {
                "id": str(row.id),
                "org_id": str(row.org_id),
                "org_name": row.name,
                "label": row.label,
                "age_days": age_days,
                "created_at": row.created_at,
                "stale": age_days > threshold,
            }
        )

    return {
        "max_age_days": threshold,
        "total_keys": len(keys),
        "stale_keys": sum(1 for key in keys if key["stale"]),
        "keys": keys,
    }


@router.get("/keys/hygiene", response_model=KeyHygieneResponse)
def api_key_age_hygiene_rollup(
    max_age_days: int = Query(default=90),
    session: Session = Depends(get_session),
) -> dict:
    return get_key_hygiene_rollup(session=session, max_age_days=max_age_days)


if __name__ == "__main__":
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
    TestSession = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    Org.__table__.create(bind=test_engine)
    ApiKey.__table__.create(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as seed_session:
        org = Org(
            id="org-hygiene-test",
            name="Hygiene Test Organization",
            created_at=now,
        )
        seed_session.add(org)
        seed_session.flush()

        seed_session.add_all(
            [
                ApiKey(
                    id="key-old",
                    org_id=org.id,
                    key_hash="a" * 64,
                    label="old key",
                    created_at=now - timedelta(days=45),
                ),
                ApiKey(
                    id="key-recent",
                    org_id=org.id,
                    key_hash="b" * 64,
                    label="recent key",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-new",
                    org_id=org.id,
                    key_hash="c" * 64,
                    label="new key",
                    created_at=now - timedelta(days=2),
                ),
            ]
        )
        seed_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/keys/hygiene",
                params={"max_age_days": 30},
            )
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["max_age_days"] == 30
            assert payload["total_keys"] == 3
            assert payload["stale_keys"] == 1
            assert len(payload["keys"]) == 3
            assert all("key_hash" not in key for key in payload["keys"])
            assert sum(1 for key in payload["keys"] if key["stale"]) == 1

        print("PASS")
    finally:
        test_engine.dispose()