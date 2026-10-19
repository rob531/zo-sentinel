# services/staged/api_key_audit_log_api/contract.py
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org

router = APIRouter(prefix="/api")


class KeyInfo(BaseModel):
    id: int
    label: str
    created_at: datetime


class KeysResponse(BaseModel):
    keys: List[KeyInfo]


@router.get("/orgs/{org_id}/keys", response_model=KeysResponse)
def list_api_keys(org_id: int, session: Session = Depends(get_session)):
    rows = (
        session.query(ApiKey.id, ApiKey.label, ApiKey.created_at)
        .filter(ApiKey.org_id == org_id)
        .all()
    )
    return KeysResponse(
        keys=[KeyInfo(id=row.id, label=row.label, created_at=row.created_at) for row in rows]
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.api_key_audit_log_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app with an in‑memory SQLite DB for testing
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # SQLite engine with StaticPool so the same connection is reused
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables for the models we use
    ApiKey.metadata.create_all(test_engine)
    Org.metadata.create_all(test_engine)

    # Dependency override to use the test session
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Seed data: two orgs, each with one API key
    with TestSessionLocal() as db:
        org1 = Org(id=1, name="Org One", created_at=datetime.utcnow())
        org2 = Org(id=2, name="Org Two", created_at=datetime.utcnow())
        db.add_all([org1, org2])
        db.flush()  # ensure org IDs are available for FK constraints

        key1 = ApiKey(
            id=1,
            org_id=org1.id,
            key_hash="hash_one",
            label="Key One",
            created_at=datetime.utcnow(),
        )
        key2 = ApiKey(
            id=2,
            org_id=org2.id,
            key_hash="hash_two",
            label="Key Two",
            created_at=datetime.utcnow(),
        )
        db.add_all([key1, key2])
        db.commit()

    client = TestClient(app)

    # Org 1 should return only its key
    resp1 = client.get("/api/orgs/1/keys")
    assert resp1.status_code == 200, f"Unexpected status {resp1.status_code}"
    data1 = resp1.json()
    assert isinstance(data1, dict) and "keys" in data1
    assert len(data1["keys"]) == 1
    assert data1["keys"][0]["id"] == 1
    assert data1["keys"][0]["label"] == "Key One"

    # Org 2 should return only its key
    resp2 = client.get("/api/orgs/2/keys")
    assert resp2.status_code == 200, f"Unexpected status {resp2.status_code}"
    data2 = resp2.json()
    assert isinstance(data2, dict) and "keys" in data2
    assert len(data2["keys"]) == 1
    assert data2["keys"][0]["id"] == 2
    assert data2["keys"][0]["label"] == "Key Two"

    print("PASS")