from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org, User


class OrgInventoryItem(BaseModel):
    org_id: str
    name: str
    created_at: str
    user_count: int
    by_role: Dict[str, int] = Field(default_factory=dict)
    api_key_count: int
    last_user_created_at: Optional[str]


class OrgInventoryResponse(BaseModel):
    generated_at: str
    org_count: int
    orgs: List[OrgInventoryItem]


router = APIRouter(prefix="/api")


def _iso_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


@router.get("/orgs/inventory", response_model=OrgInventoryResponse)
def get_org_inventory(
    include_empty: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> OrgInventoryResponse:
    orgs = session.execute(
        select(Org).order_by(Org.created_at, Org.id)
    ).scalars().all()
    org_ids = [str(org.id) for org in orgs]

    role_rows = session.execute(
        select(User.org_id, User.role, func.count(User.id).label("user_count"))
        .where(User.org_id.in_(org_ids))
        .group_by(User.org_id, User.role)
    ).all()

    latest_rows = session.execute(
        select(User.org_id, func.max(User.created_at).label("last_user_created_at"))
        .where(User.org_id.in_(org_ids))
        .group_by(User.org_id)
    ).all()

    api_key_rows = session.execute(
        select(ApiKey.org_id, func.count(ApiKey.id).label("api_key_count"))
        .where(ApiKey.org_id.in_(org_ids))
        .group_by(ApiKey.org_id)
    ).all()

    roles_by_org: Dict[str, Dict[str, int]] = {}
    user_counts: Dict[str, int] = {}
    for org_id, role, count in role_rows:
        key = str(org_id)
        roles_by_org.setdefault(key, {})[str(role)] = int(count)
        user_counts[key] = user_counts.get(key, 0) + int(count)

    latest_by_org = {
        str(org_id): last_user_created_at
        for org_id, last_user_created_at in latest_rows
    }
    api_keys_by_org = {
        str(org_id): int(count)
        for org_id, count in api_key_rows
    }

    items: List[OrgInventoryItem] = []
    for org in orgs:
        org_id = str(org.id)
        user_count = user_counts.get(org_id, 0)
        if not include_empty and user_count == 0:
            continue
        items.append(
            OrgInventoryItem(
                org_id=org_id,
                name=org.name,
                created_at=_iso_datetime(org.created_at),
                user_count=user_count,
                by_role=roles_by_org.get(org_id, {}),
                api_key_count=api_keys_by_org.get(org_id, 0),
                last_user_created_at=_iso_datetime(
                    latest_by_org.get(org_id)
                ),
            )
        )

    return OrgInventoryResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        org_count=len(items),
        orgs=items,
    )


app = FastAPI(title="Organization User Inventory API")
app.include_router(router)


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    Org.__table__.create(engine)
    User.__table__.create(engine)
    ApiKey.__table__.create(engine)

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        session.add_all(
            [
                Org(id="org-alpha", name="Alpha Industries", created_at=now),
                Org(id="org-beta", name="Beta Systems", created_at=now),
                Org(id="org-empty", name="Empty Organization", created_at=now),
                User(
                    id="user-1",
                    email="admin@alpha.example",
                    org_id="org-alpha",
                    role="admin",
                    password_hash="test-hash-1",
                    created_at=now,
                ),
                User(
                    id="user-2",
                    email="member@alpha.example",
                    org_id="org-alpha",
                    role="member",
                    password_hash="test-hash-2",
                    created_at=now,
                ),
                User(
                    id="user-3",
                    email="viewer@alpha.example",
                    org_id="org-alpha",
                    role="viewer",
                    password_hash="test-hash-3",
                    created_at=now,
                ),
                User(
                    id="user-4",
                    email="admin@beta.example",
                    org_id="org-beta",
                    role="admin",
                    password_hash="test-hash-4",
                    created_at=now,
                ),
                User(
                    id="user-5",
                    email="member@beta.example",
                    org_id="org-beta",
                    role="member",
                    password_hash="test-hash-5",
                    created_at=now,
                ),
                ApiKey(
                    id="key-1",
                    org_id="org-alpha",
                    key_hash="hash-1",
                    label="Primary",
                    created_at=now,
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-alpha",
                    key_hash="hash-2",
                    label="Secondary",
                    created_at=now,
                ),
                ApiKey(
                    id="key-3",
                    org_id="org-beta",
                    key_hash="hash-3",
                    label="Primary",
                    created_at=now,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/inventory", params={"include_empty": "true"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["org_count"] == 3
        assert len(payload["orgs"]) == 3
        assert any(org["user_count"] == 3 for org in payload["orgs"])

        empty_orgs = [org for org in payload["orgs"] if org["user_count"] == 0]
        assert len(empty_orgs) == 1
        assert empty_orgs[0]["api_key_count"] == 0

        for org in payload["orgs"]:
            assert org["name"]
            assert isinstance(org["by_role"], dict)
            assert "key_hash" not in org

    print("PASS")


if __name__ == "__main__":
    run_self_test()