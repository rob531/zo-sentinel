from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User

from .logic import get_org_user_apikey_capacity


router = APIRouter(prefix="/api")


class OrgCapacity(BaseModel):
    org_id: int
    name: str
    users: int
    roles: dict[str, int]
    api_keys: int
    keys_per_user: float
    no_key_flag: bool


class CapacitySummary(BaseModel):
    orgs: int
    users: int
    api_keys: int
    orgs_without_keys: int


class UserKeyCapacityResponse(BaseModel):
    orgs: list[OrgCapacity]
    summary: CapacitySummary


@router.get(
    "/orgs/user-key-capacity",
    response_model=UserKeyCapacityResponse,
)
def get_user_key_capacity(session: Session = Depends(get_session)):
    return get_org_user_apikey_capacity(session)


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    Org.__table__.create(test_engine)
    User.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    test_session = TestSession()
    now = datetime.now(timezone.utc)

    test_session.add_all(
        [
            Org(id=1, name="Organization One", created_at=now),
            Org(id=2, name="Organization Two", created_at=now),
            User(
                id=1,
                org_id=1,
                email="one@example.test",
                role="admin",
                password_hash="test",
                created_at=now,
            ),
            User(
                id=2,
                org_id=2,
                email="two@example.test",
                role="member",
                password_hash="test",
                created_at=now,
            ),
            User(
                id=3,
                org_id=2,
                email="three@example.test",
                role="member",
                password_hash="test",
                created_at=now,
            ),
            ApiKey(
                id=1,
                org_id=1,
                key_hash="key-one",
                label="Primary",
                created_at=now,
            ),
            ApiKey(
                id=2,
                org_id=1,
                key_hash="key-two",
                label="Secondary",
                created_at=now,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    response = TestClient(test_app).get("/api/orgs/user-key-capacity")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert len(payload["orgs"]) == 2
    assert sum(1 for org in payload["orgs"] if org["no_key_flag"]) == 1
    assert payload["summary"]["orgs"] == 2
    assert payload["summary"]["users"] == 3
    assert payload["summary"]["api_keys"] == 2
    assert payload["summary"]["orgs_without_keys"] == 1

    test_session.close()
    test_engine.dispose()
    print("PASS")