from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey


router = APIRouter(prefix="/api", tags=["api-key-age"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.isoformat()


def api_key_age_rollup(
    db: Session | None = None,
    org_id: str | None = None,
    stale_days: int = 90,
) -> dict[str, Any]:
    """Return an organization-scoped age and staleness rollup for API keys."""
    if db is None:
        raise ValueError("A database session is required")

    query = select(ApiKey.id, ApiKey.org_id, ApiKey.label, ApiKey.created_at)
    if org_id is not None:
        query = query.where(ApiKey.org_id == org_id)

    rows = db.execute(query).all()
    now = datetime.now(timezone.utc)
    keys: list[dict[str, Any]] = []

    for key_id, key_org_id, label, created_at in rows:
        created = _as_utc(created_at)
        age_days = (now - created).days
        keys.append(
            {
                "id": str(key_id),
                "org_id": str(key_org_id),
                "label": str(label),
                "age_days": int(age_days),
                "created_at": _format_timestamp(created),
                "stale": bool(age_days >= stale_days),
            }
        )

    keys.sort(key=lambda item: item["age_days"], reverse=True)

    return {
        "stale_days": int(stale_days),
        "org_id": org_id,
        "keys": keys,
        "total": len(keys),
        "stale_total": sum(1 for item in keys if item["stale"]),
    }


@router.get("/orgs/api-key-age")
def get_api_key_age(
    org_id: str | None = None,
    stale_days: int = 90,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return api_key_age_rollup(db=db, org_id=org_id, stale_days=stale_days)


if __name__ == "__main__":
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
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    ApiKey.__table__.create(engine)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                ApiKey(
                    id="key-stale",
                    org_id="org-a",
                    key_hash="hash-stale-legacy",
                    label="stale-legacy",
                    created_at=now - timedelta(days=200),
                ),
                ApiKey(
                    id="key-fresh",
                    org_id="org-a",
                    key_hash="hash-fresh",
                    label="fresh",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-other-org",
                    org_id="org-b",
                    key_hash="hash-other-org",
                    label="other-org",
                    created_at=now - timedelta(days=120),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/orgs/api-key-age")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["stale_total"] == 2
        assert body["keys"][0]["age_days"] >= 100
        assert "hash-stale-legacy" not in response.text
        assert "hash-fresh" not in response.text
        assert "hash-other-org" not in response.text

        filtered = client.get("/api/orgs/api-key-age", params={"org_id": "org-a"})
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 2

    print("PASS")