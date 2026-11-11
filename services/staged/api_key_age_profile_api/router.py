from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org
from .logic import get_api_key_age_profile

router = APIRouter(prefix="/api", tags=["api-keys"])


class ApiKeyAgeBucket(BaseModel):
    bucket: str
    count: int
    pct: float


class ApiKeyAgeProfileResponse(BaseModel):
    total: int
    buckets: List[ApiKeyAgeBucket]
    by_org: List[Dict[str, Any]]
    oldest_created_at: Optional[datetime]


@router.get(
    "/api-keys/age-profile",
    response_model=ApiKeyAgeProfileResponse,
    operation_id="get_api_key_age_profile",
)
def api_key_age_profile(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_api_key_age_profile(session)


if __name__ == "__main__":
    import uuid

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Integer, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(bind=test_engine)
    ApiKey.__table__.create(bind=test_engine)

    TestSession = sessionmaker(bind=test_engine)

    def make_id(column):
        if isinstance(column.type, Integer):
            return 1
        return uuid.uuid4().hex

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = TestSession()
    try:
        org_one_id = make_id(Org.__table__.c.id)
        org_two_id = make_id(Org.__table__.c.id)
        db.add_all(
            [
                Org(
                    id=org_one_id,
                    name="Test Org One",
                    created_at=now - timedelta(days=400),
                ),
                Org(
                    id=org_two_id,
                    name="Test Org Two",
                    created_at=now - timedelta(days=200),
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                ApiKey(
                    id=make_id(ApiKey.__table__.c.id),
                    org_id=org_one_id,
                    key_hash="test-hash-1",
                    label="Ten days old",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id=make_id(ApiKey.__table__.c.id),
                    org_id=org_one_id,
                    key_hash="test-hash-2",
                    label="Sixty days old",
                    created_at=now - timedelta(days=60),
                ),
                ApiKey(
                    id=make_id(ApiKey.__table__.c.id),
                    org_id=org_two_id,
                    key_hash="test-hash-3",
                    label="Two hundred days old",
                    created_at=now - timedelta(days=200),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/api-keys/age-profile")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total"]
    counts = {bucket["bucket"]: bucket["count"] for bucket in payload["buckets"]}
    assert counts["0-30d"] == 1
    assert counts["31-90d"] == 1
    assert counts["91-180d"] == 0
    assert counts["181-365d"] == 1
    assert counts["365d+"] == 0
    assert sorted(org["key_count"] for org in payload["by_org"]) == [1, 2]
    print("PASS")