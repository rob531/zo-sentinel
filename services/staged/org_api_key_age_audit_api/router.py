"""HTTP surface for organization API key age and staleness audits."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

from .logic import audit_api_keys

router = APIRouter(prefix="/api", tags=["org-api-key-age-audit"])


class ApiKeyAuditItem(BaseModel):
    id: int
    label: str
    key_hash_prefix: str
    created_at: datetime
    age_days: float
    stale: bool


class ApiKeyAuditResponse(BaseModel):
    org_id: int
    org_name: str
    total_keys: int
    stale_keys: int
    stale_threshold_days: int
    keys: list[ApiKeyAuditItem]


@router.get(
    "/orgs/{org_id}/api-keys/audit",
    response_model=ApiKeyAuditResponse,
)
def get_org_api_key_audit(
    org_id: int,
    stale_threshold_days: int = 90,
    db: Session = Depends(get_session),
) -> ApiKeyAuditResponse:
    result = audit_api_keys(db, org_id, stale_threshold_days)
    return ApiKeyAuditResponse(**result)


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
    ApiKey.__table__.create(test_engine)
    Org.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    with TestSession() as session:
        session.add(Org(id=1, name="Example Organization"))
        session.add_all(
            [
                ApiKey(
                    id=1,
                    org_id=1,
                    key_hash="stale-key-hash-value",
                    label="stale",
                    created_at=datetime.utcnow() - timedelta(days=200),
                ),
                ApiKey(
                    id=2,
                    org_id=1,
                    key_hash="recent-key-hash-one",
                    label="recent-one",
                    created_at=datetime.utcnow() - timedelta(days=10),
                ),
                ApiKey(
                    id=3,
                    org_id=1,
                    key_hash="recent-key-hash-two",
                    label="recent-two",
                    created_at=datetime.utcnow() - timedelta(days=5),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/orgs/1/api-keys/audit",
        params={"stale_threshold_days": 90},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_keys"] == 3, payload
    assert payload["stale_keys"] == 1, payload
    stale = [key for key in payload["keys"] if key["stale"]]
    assert len(stale) == 1, payload
    assert stale[0]["age_days"] > 90, payload
    print("PASS")