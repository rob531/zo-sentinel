from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


STALE_AFTER_DAYS = 90


class ApiKeyAgeingItem(BaseModel):
    id: str
    org_id: str
    org_name: str
    label: str
    created_at: str
    age_days: int
    stale: bool


class ApiKeyAgeingResponse(BaseModel):
    total: int
    stale_after_days: int = Field(default=STALE_AFTER_DAYS)
    keys: list[ApiKeyAgeingItem]


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def list_api_keys(
    db: Session,
    org_id: Optional[str] = None,
) -> list[dict[str, object]]:
    statement = (
        select(ApiKey, Org.name)
        .select_from(ApiKey)
        .join(Org, ApiKey.org_id == Org.id)
        .order_by(ApiKey.created_at.asc())
    )
    if org_id is not None:
        statement = statement.where(ApiKey.org_id == org_id)

    now = datetime.now(timezone.utc)
    result: list[dict[str, object]] = []

    for api_key, org_name in db.execute(statement).all():
        created_at = _parse_timestamp(api_key.created_at)
        age_days = (now - created_at).days
        result.append(
            {
                "id": str(api_key.id),
                "org_id": str(api_key.org_id),
                "org_name": str(org_name),
                "label": str(api_key.label),
                "created_at": created_at.isoformat(),
                "age_days": age_days,
                "stale": age_days > STALE_AFTER_DAYS,
            }
        )

    return result


def create_app() -> FastAPI:
    application = FastAPI()

    @application.get(
        "/api/orgs/api-keys",
        response_model=ApiKeyAgeingResponse,
    )
    def get_org_api_keys(
        org_id: Optional[str] = Query(default=None),
        db: Session = Depends(get_session),
    ) -> ApiKeyAgeingResponse:
        keys = list_api_keys(db, org_id)
        return ApiKeyAgeingResponse(
            total=len(keys),
            stale_after_days=STALE_AFTER_DAYS,
            keys=[ApiKeyAgeingItem(**key) for key in keys],
        )

    return application


app = create_app()


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        db.add_all(
            [
                Org(id="org-1", name="First Organisation", created_at=now),
                Org(id="org-2", name="Second Organisation", created_at=now),
            ]
        )
        db.flush()
        db.add_all(
            [
                ApiKey(
                    id="key-old",
                    org_id="org-1",
                    label="old-key",
                    key_hash="secret-old-hash-must-not-be-returned",
                    created_at=now - timedelta(days=120),
                ),
                ApiKey(
                    id="key-recent-1",
                    org_id="org-1",
                    label="recent-key-1",
                    key_hash="secret-recent-hash-1",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-recent-2",
                    org_id="org-2",
                    label="recent-key-2",
                    key_hash="secret-recent-hash-2",
                    created_at=now - timedelta(days=3),
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with SessionLocal() as db:
            yield db

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/api-keys")
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["total"] == 3, payload
        assert payload["stale_after_days"] == 90, payload
        assert len(payload["keys"]) == 3, payload
        assert all("key_hash" not in key for key in payload["keys"]), payload
        assert sum(1 for key in payload["keys"] if key["stale"]) == 1, payload

        filtered = client.get("/api/orgs/api-keys", params={"org_id": "org-2"})
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert filtered_payload["total"] == 1, filtered_payload
        assert filtered_payload["keys"][0]["org_id"] == "org-2", filtered_payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()