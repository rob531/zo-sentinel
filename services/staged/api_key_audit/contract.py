from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import List
from datetime import datetime

from app.db import get_session
from app.models import ApiKey, User, Org

class ApiKeyAuditEntry(BaseModel):
    id: int
    org_id: int
    label: str
    created_at: datetime
    user_email: str

    class Config:
        from_attributes = True

class ApiKeyAuditResponse(BaseModel):
    keys: List[ApiKeyAuditEntry]

the_app = FastAPI()

@the_app.get("/api/admin/api-keys/audit", response_model=ApiKeyAuditResponse)
def get_api_key_audit(db: Session = Depends(get_session)):
    stmt = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.label,
            ApiKey.created_at,
            User.email.label("user_email")
        )
        .select_from(ApiKey)
        .join(User, ApiKey.org_id == User.org_id)
    )
    results = db.execute(stmt).all()
    keys = [
        ApiKeyAuditEntry(
            id=row.id,
            org_id=row.org_id,
            label=row.label,
            created_at=row.created_at,
            user_email=row.user_email
        )
        for row in results
    ]
    return ApiKeyAuditResponse(keys=keys)

if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    the_app.dependency_overrides[get_session] = override_get_session

    with test_engine.connect() as conn:
        conn.execute(text("CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT, created_at TIMESTAMP)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, org_id INTEGER, role TEXT, password_hash TEXT, clerk_id TEXT, clerk_created_at TIMESTAMP, clerk_synced_via TEXT, created_at TIMESTAMP)"))
        conn.execute(text("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, org_id INTEGER, key_hash TEXT, label TEXT, created_at TIMESTAMP)"))
        conn.commit()

    client = TestClient(the_app)

    db = TestingSessionLocal()
    org1 = Org(id=1, name="Org One", created_at=datetime.now())
    org2 = Org(id=2, name="Org Two", created_at=datetime.now())
    db.add(org1)
    db.add(org2)

    user1 = User(id=1, email="user1@example.com", org_id=1, role="member", password_hash="x", created_at=datetime.now())
    user2 = User(id=2, email="user2@example.com", org_id=2, role="member", password_hash="x", created_at=datetime.now())
    db.add(user1)
    db.add(user2)

    key1 = ApiKey(id=1, org_id=1, key_hash="hash1", label="Production Key", created_at=datetime.now())
    key2 = ApiKey(id=2, org_id=2, key_hash="hash2", label="Staging Key", created_at=datetime.now())
    db.add(key1)
    db.add(key2)
    db.commit()
    db.close()

    response = client.get("/api/admin/api-keys/audit")
    assert response.status_code == 200
    data = response.json()
    assert len(data["keys"]) == 2
    assert "label" in data["keys"][0]

    print("PASS")