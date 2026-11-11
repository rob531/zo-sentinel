from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


class ApiKeyOut(BaseModel):
    id: str
    label: Optional[str]
    key_hash_prefix: str
    created_at: datetime
    age_days: int


class ApiKeyListOut(BaseModel):
    org_id: str
    org_name: str
    keys: List[ApiKeyOut]
    total: int
    oldest_age_days: int


router = APIRouter(prefix="/api")


def _age_days(created_at) -> int:
    if isinstance(created_at, str):
        normalized = created_at.strip().replace("Z", "+00:00")
        created_at = datetime.fromisoformat(normalized)
    elif isinstance(created_at, date) and not isinstance(created_at, datetime):
        created_at = datetime.combine(created_at, datetime.min.time())

    if created_at.tzinfo is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        now = datetime.now(timezone.utc)
        created_at = created_at.astimezone(timezone.utc)

    return max(0, (now - created_at).days)


def get_api_key_lifecycle(org_id: str, session: Session) -> dict:
    org_row = session.execute(
        select(Org.id, Org.name).where(Org.id == org_id)
    ).one_or_none()
    if org_row is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    rows = session.execute(
        select(
            ApiKey.id,
            ApiKey.label,
            ApiKey.created_at,
            func.substr(ApiKey.key_hash, 1, 8).label("key_hash_prefix"),
        )
        .where(ApiKey.org_id == org_id)
        .order_by(ApiKey.created_at.asc(), ApiKey.id.asc())
    ).all()

    keys = []
    for row in rows:
        keys.append(
            {
                "id": str(row.id),
                "label": row.label,
                "key_hash_prefix": str(row.key_hash_prefix or "")[:8],
                "created_at": row.created_at,
                "age_days": _age_days(row.created_at),
            }
        )

    oldest_age_days = max((key["age_days"] for key in keys), default=0)
    return {
        "org_id": str(org_row.id),
        "org_name": org_row.name,
        "keys": keys,
        "total": len(keys),
        "oldest_age_days": oldest_age_days,
    }


def list_api_keys(org_id: str, session: Session = Depends(get_session)) -> dict:
    return get_api_key_lifecycle(org_id, session)


def get_api_keys(session: Session, org_id: str) -> dict:
    return get_api_key_lifecycle(org_id, session)


@router.get("/orgs/{org_id}/api-keys", response_model=ApiKeyListOut)
def api_key_lifecycle(org_id: str, session: Session = Depends(get_session)) -> dict:
    return get_api_key_lifecycle(org_id, session)


if __name__ == "__main__":
    import json
    from datetime import timedelta

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
    Org.__table__.create(engine, checkfirst=True)
    ApiKey.__table__.create(engine, checkfirst=True)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    org_a_hash = "org-a-key-hash-0000000000000001"
    org_a_hash_2 = "org-a-key-hash-0000000000000002"
    org_b_hash = "org-b-key-hash-0000000000000003"

    with SessionLocal() as session:
        session.add_all(
            [
                Org(id="org-a", name="Organization A", created_at=now),
                Org(id="org-b", name="Organization B", created_at=now),
                ApiKey(
                    id="key-a-1",
                    org_id="org-a",
                    key_hash=org_a_hash,
                    label="A key one",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-a-2",
                    org_id="org-a",
                    key_hash=org_a_hash_2,
                    label="A key two",
                    created_at=now - timedelta(days=3),
                ),
                ApiKey(
                    id="key-b-1",
                    org_id="org-b",
                    key_hash=org_b_hash,
                    label="B key one",
                    created_at=now - timedelta(days=5),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/org-a/api-keys")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["org_id"] == "org-a"
        assert payload["org_name"] == "Organization A"
        assert payload["total"] == 2
        assert len(payload["keys"]) == 2
        assert payload["oldest_age_days"] == 10
        assert all(len(key["key_hash_prefix"]) <= 8 for key in payload["keys"])
        assert org_a_hash not in json.dumps(payload)
        assert org_a_hash_2 not in json.dumps(payload)
        assert org_b_hash not in json.dumps(payload)
        assert "key-b-1" not in json.dumps(payload)

    print("PASS")