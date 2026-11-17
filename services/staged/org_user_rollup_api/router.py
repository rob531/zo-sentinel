from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org, User

try:
    from .logic import get_org_rollup
except ImportError:
    try:
        from .logic import get_org_user_rollup as get_org_rollup
    except ImportError:
        from .logic import fetch_org_rollup as get_org_rollup

router = APIRouter(prefix="/api", tags=["org-user-rollup"])


class OrgRollupItem(BaseModel):
    org_id: str | int
    name: str
    created_at: datetime
    user_count: int
    api_key_count: int


class OrgRollupResponse(BaseModel):
    orgs: list[OrgRollupItem]


def _as_rollup_item(value: Any) -> OrgRollupItem:
    if isinstance(value, dict):
        data = value
    elif hasattr(value, "model_dump"):
        data = value.model_dump()
    elif hasattr(value, "_asdict"):
        data = value._asdict()
    else:
        data = vars(value)

    if hasattr(OrgRollupItem, "model_validate"):
        return OrgRollupItem.model_validate(data)
    return OrgRollupItem.parse_obj(data)


@router.get("/org/rollup", response_model=OrgRollupResponse)
async def org_rollup(
    session: Session = Depends(get_session),
) -> OrgRollupResponse:
    result = get_org_rollup(session)
    if inspect.isawaitable(result):
        result = await result

    if isinstance(result, dict):
        result = result.get("orgs", [])

    return OrgRollupResponse(orgs=[_as_rollup_item(item) for item in result])


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Org.__table__.create(engine)
    User.__table__.create(engine)
    ApiKey.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    org_one_id = str(uuid4())
    org_two_id = str(uuid4())

    with SessionLocal() as session:
        session.add_all(
            [
                Org(id=org_one_id, name="Organization One", created_at=now),
                Org(
                    id=org_two_id,
                    name="Organization Two",
                    created_at=now + timedelta(seconds=1),
                ),
            ]
        )
        session.flush()

        session.add_all(
            [
                User(
                    id=str(uuid4()),
                    org_id=org_one_id,
                    email="one@example.test",
                    password_hash="password-hash",
                    role="member",
                    created_at=now,
                    clerk_id=str(uuid4()),
                    clerk_created_at=now,
                    clerk_synced_via="self-test",
                ),
                User(
                    id=str(uuid4()),
                    org_id=org_one_id,
                    email="two@example.test",
                    password_hash="password-hash",
                    role="member",
                    created_at=now,
                    clerk_id=str(uuid4()),
                    clerk_created_at=now,
                    clerk_synced_via="self-test",
                ),
                User(
                    id=str(uuid4()),
                    org_id=org_two_id,
                    email="three@example.test",
                    password_hash="password-hash",
                    role="member",
                    created_at=now,
                    clerk_id=str(uuid4()),
                    clerk_created_at=now,
                    clerk_synced_via="self-test",
                ),
            ]
        )
        session.add_all(
            [
                ApiKey(
                    id=1,
                    org_id=org_one_id,
                    key_hash="key-hash-one",
                    label="Key One",
                    created_at=now,
                ),
                ApiKey(
                    id=2,
                    org_id=org_one_id,
                    key_hash="key-hash-two",
                    label="Key Two",
                    created_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/org/rollup")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["orgs"]) == 2
        assert payload["orgs"][0]["user_count"] == 2
        assert payload["orgs"][0]["api_key_count"] == 2
        assert payload["orgs"][1]["user_count"] == 1
        assert payload["orgs"][1]["api_key_count"] == 0

    print("PASS")