from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import audit_api_keys as audit_api_keys_logic


router = APIRouter(prefix="/api")


class OrgApiKeyAuditItem(BaseModel):
    org_id: str
    org_name: str
    key_count: int
    distinct_labels: int
    oldest_key_created_at: str | None
    newest_key_created_at: str | None


class OrgApiKeyAuditResponse(BaseModel):
    total_keys: int
    total_orgs: int
    orgs: list[OrgApiKeyAuditItem]


@router.get("/orgs/api-keys", response_model=OrgApiKeyAuditResponse)
def audit_api_keys(session: Session = Depends(get_session)) -> OrgApiKeyAuditResponse:
    return OrgApiKeyAuditResponse.model_validate(audit_api_keys_logic(session))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import ApiKey, Org

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    test_session_factory = sessionmaker(bind=engine)
    test_session = test_session_factory()

    test_session.add_all(
        [
            Org(id="org-1", name="Organisation One"),
            Org(id="org-2", name="Organisation Two"),
        ]
    )
    test_session.flush()
    test_session.add_all(
        [
            ApiKey(
                id="key-1",
                org_id="org-1",
                key_hash="hash-1",
                label="production",
            ),
            ApiKey(
                id="key-2",
                org_id="org-1",
                key_hash="hash-2",
                label="staging",
            ),
            ApiKey(
                id="key-3",
                org_id="org-1",
                key_hash="hash-3",
                label="production",
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    response = TestClient(test_app).get("/api/orgs/api-keys")
    response.raise_for_status()
    payload = response.json()

    assert payload["total_keys"] == 3
    assert payload["total_orgs"] == 2
    assert any(
        org["org_id"] == "org-2" and org["key_count"] == 0
        for org in payload["orgs"]
    )
    assert "key_hash" not in response.text

    test_session.close()
    engine.dispose()
    print("PASS")